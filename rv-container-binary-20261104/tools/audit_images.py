#!/usr/bin/env python3
"""Inspect OCI metadata, hashes and compiled MPI payload without running images."""
import hashlib
import json
from pathlib import Path
import shutil
import struct
import tarfile
import tempfile
from release import ROOT


def inspect(path, communication=False, static_binary=None):
    captured = {}
    with tarfile.open(path, 'r:*') as archive:
        def data(name):
            return archive.extractfile(name).read()
        def blob(descriptor):
            name = 'blobs/sha256/' + descriptor['digest'].split(':', 1)[1]
            value = data(name)
            if hashlib.sha256(value).hexdigest() != descriptor['digest'].split(':', 1)[1]:
                raise ValueError('OCI metadata hash mismatch: ' + name)
            return json.loads(value)
        index = json.loads(data('index.json'))
        descriptor = index['manifests'][0]
        manifest = blob(descriptor)
        config = blob(manifest['config'])
        if config.get('architecture') != 'riscv64' or config.get('os') != 'linux':
            raise ValueError('expected Linux/RISC-V64 image')
        layers = []
        for layer in manifest['layers']:
            name = 'blobs/sha256/' + layer['digest'].split(':', 1)[1]
            digest = hashlib.sha256()
            with tempfile.TemporaryFile() as temporary:
                with archive.extractfile(name) as stream:
                    for part in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(part)
                        temporary.write(part)
                if digest.hexdigest() != layer['digest'].split(':', 1)[1]:
                    raise ValueError('OCI layer hash mismatch')
                temporary.seek(0)
                if communication or static_binary:
                    with tarfile.open(fileobj=temporary, mode='r:*') as filesystem:
                        wanted = {'usr/local/bin/rv-bench', 'opt/rv-communication/benchmark.c',
                                  'opt/rv-communication/rv_collective.c', 'opt/rv-communication/rv_collective.h',
                                  'opt/rv-communication/launch.py'}
                        if static_binary:
                            wanted.add(static_binary[0])
                        for member in filesystem:
                            key = member.name.removeprefix('./')
                            if key in wanted and member.isfile():
                                captured[key] = filesystem.extractfile(member).read()
                layers.append({'digest': layer['digest'], 'bytes': layer['size'], 'verified': True})
        result = {'file': path.relative_to(ROOT).as_posix(), 'manifestDigest': descriptor['digest'],
                  'architecture': 'riscv64', 'os': 'linux', 'layers': layers,
                  'imageAnnotations': descriptor.get('annotations', {}), 'configDigest': manifest['config']['digest']}
        if communication:
            executable = captured.get('usr/local/bin/rv-bench', b'')
            if executable[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', executable, 18)[0] != 243:
                raise ValueError('compiled RISC-V MPI executable missing')
            checked = []
            for name in ('benchmark.c', 'rv_collective.c', 'rv_collective.h', 'launch.py'):
                source = ROOT / 'source/riscv-platform-components/platform/communication' / name
                payload = captured.get('opt/rv-communication/' + name)
                if payload != source.read_bytes():
                    raise ValueError('image/source mismatch: ' + name)
                checked.append(name)
            result.update(mpiExecutableSha256=hashlib.sha256(executable).hexdigest(),
                          matchingSourceFiles=checked,
                          executionScope='rv-bench runs inside this image with its matched MPI/libc runtime; not a host binary')
        if static_binary:
            payload = captured.get(static_binary[0])
            if payload != (ROOT / static_binary[1]).read_bytes():
                raise ValueError('static image/binary mismatch: ' + static_binary[0])
            result['executableSha256'] = hashlib.sha256(payload).hexdigest()
        return result


def main():
    images = [inspect(ROOT / 'offline/communication/rv-communication-0.2.0.tar.gz', True),
              inspect(ROOT / 'offline/monitoring/rv-monitor-core-0.1.0-linux-riscv64.oci.tar',
                      static_binary=('bin/rv-monitor-core', 'source/monitor-core/bin/rv-monitor-core-linux-riscv64')),
              inspect(ROOT / 'offline/workload/rv-test-workload-20261104-linux-riscv64.oci.tar',
                      static_binary=('bin/rv-test-workload', 'source/riscv-platform-components/platform/workload/bin/rv-test-workload'))]
    result = {'passed': True, 'images': images, 'hardwareExecution': 'not performed'}
    (ROOT / 'IMAGE-CONTENTS.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
