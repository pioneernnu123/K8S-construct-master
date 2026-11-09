#!/usr/bin/env python3
"""Package the static RISC-V binary as an OCI image without Docker/build services."""
from pathlib import Path
import argparse
import hashlib
import io
import json
import tarfile

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    output = args.output or root/'rv-monitor-core-0.1.0-linux-riscv64.oci.tar'
    binary = (root/'bin/rv-monitor-core-linux-riscv64').read_bytes()
    if binary[:6] != b'\x7fELF\x02\x01' or int.from_bytes(binary[18:20], 'little') != 243:
        raise SystemExit('expected an ELF64 little-endian RISC-V binary')
    layer_buffer = io.BytesIO()
    with tarfile.open(fileobj=layer_buffer, mode='w') as layer:
        files = {'bin/rv-monitor-core': (binary, 0o555)}
        for path in (root/'third_party').glob('*'):
            if path.is_file():
                files['licenses/'+path.name] = (path.read_bytes(), 0o444)
        directories = sorted({parent for name in files for parent in Path(name).parents if str(parent) != '.'}, key=str)
        for directory in directories:
            info = tarfile.TarInfo(directory.as_posix())
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            layer.addfile(info)
        for name, (data, mode) in sorted(files.items()):
            info = tarfile.TarInfo(name)
            info.mode = mode
            info.size = len(data)
            layer.addfile(info, io.BytesIO(data))
    layer_data = layer_buffer.getvalue()
    blobs = {}
    def blob(data, media):
        digest = hashlib.sha256(data).hexdigest()
        blobs['blobs/sha256/'+digest] = data
        return {'mediaType': media, 'digest': 'sha256:'+digest, 'size': len(data)}
    def encode(value):
        return json.dumps(value, separators=(',', ':')).encode()
    layer_desc = blob(layer_data, 'application/vnd.oci.image.layer.v1.tar')
    config = {'architecture':'riscv64', 'os':'linux',
              'config':{'User':'65534:65534', 'Entrypoint':['/bin/rv-monitor-core'],
                        'ExposedPorts':{'9090/tcp':{}},
                        'Labels':{'org.opencontainers.image.title':'rv-monitor-core',
                                  'org.opencontainers.image.version':'0.1.0'}},
              'rootfs':{'type':'layers','diff_ids':[layer_desc['digest']]}}
    config_desc = blob(encode(config), 'application/vnd.oci.image.config.v1+json')
    manifest = {'schemaVersion':2,'mediaType':'application/vnd.oci.image.manifest.v1+json',
                'config':config_desc,'layers':[layer_desc]}
    manifest_desc = blob(encode(manifest), 'application/vnd.oci.image.manifest.v1+json')
    manifest_desc['platform'] = {'os':'linux','architecture':'riscv64'}
    manifest_desc['annotations'] = {'org.opencontainers.image.ref.name':'0.1.0',
                                    'io.containerd.image.name':'localhost/rv-monitor-core:0.1.0'}
    index = {'schemaVersion':2,'mediaType':'application/vnd.oci.image.index.v1+json','manifests':[manifest_desc]}
    with tarfile.open(output, 'w') as archive:
        for name, data in {**blobs, 'oci-layout':encode({'imageLayoutVersion':'1.0.0'}), 'index.json':encode(index)}.items():
            info = tarfile.TarInfo(name)
            info.mode = 0o444
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    print(output)

if __name__ == '__main__':
    main()
