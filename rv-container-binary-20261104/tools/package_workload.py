#!/usr/bin/env python3
"""Create the self-contained RISC-V acceptance workload OCI image."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
from release import ROOT


def main():
    executable = (ROOT / 'source/riscv-platform-components/platform/workload/bin/rv-test-workload').read_bytes()
    if executable[:6] != b'\x7fELF\x02\x01' or int.from_bytes(executable[18:20], 'little') != 243:
        raise ValueError('expected ELF64 RISC-V workload')
    layer = io.BytesIO()
    with tarfile.open(fileobj=layer, mode='w') as archive:
        for name, mode in [('bin', 0o755), ('tmp', 0o1777)]:
            entry = tarfile.TarInfo(name)
            entry.type = tarfile.DIRTYPE
            entry.mode = mode
            archive.addfile(entry)
        entry = tarfile.TarInfo('bin/rv-test-workload')
        entry.mode = 0o555
        entry.size = len(executable)
        archive.addfile(entry, io.BytesIO(executable))
    blobs = {}
    def encode(value):
        return json.dumps(value, separators=(',', ':')).encode()
    def blob(value, media):
        digest = hashlib.sha256(value).hexdigest()
        blobs['blobs/sha256/' + digest] = value
        return {'mediaType': media, 'digest': 'sha256:' + digest, 'size': len(value)}
    layer_descriptor = blob(layer.getvalue(), 'application/vnd.oci.image.layer.v1.tar')
    config = {'architecture': 'riscv64', 'os': 'linux',
              'config': {'User': '65534:65534', 'Entrypoint': ['/bin/rv-test-workload'], 'Cmd': ['hold'],
                         'ExposedPorts': {'8080/tcp': {}}},
              'rootfs': {'type': 'layers', 'diff_ids': [layer_descriptor['digest']]}}
    config_descriptor = blob(encode(config), 'application/vnd.oci.image.config.v1+json')
    manifest = blob(encode({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json',
                           'config': config_descriptor, 'layers': [layer_descriptor]}),
                    'application/vnd.oci.image.manifest.v1+json')
    manifest.update(platform={'os': 'linux', 'architecture': 'riscv64'},
                    annotations={'org.opencontainers.image.ref.name': '20261104',
                                 'io.containerd.image.name': 'localhost/rv-test-workload:20261104'})
    target = ROOT / 'offline/workload/rv-test-workload-20261104-linux-riscv64.oci.tar'
    target.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(target, 'w') as archive:
        for name, value in {**blobs, 'oci-layout': encode({'imageLayoutVersion': '1.0.0'}),
                            'index.json': encode({'schemaVersion': 2, 'manifests': [manifest]})}.items():
            entry = tarfile.TarInfo(name)
            entry.mode = 0o444
            entry.size = len(value)
            archive.addfile(entry, io.BytesIO(value))
    print(target)


if __name__ == '__main__':
    main()
