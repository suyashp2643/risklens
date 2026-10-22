"""Exact captured bytes, canonical envelopes and strict integrity verification."""
import base64
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType
from typing import Mapping
from .ingestion import load_dataset

INPUT_FILES = ('prices.csv', 'instruments.csv', 'calendar.csv', 'corporate_actions.csv',
               'portfolio.json', 'provenance.json')
HASH_FORMAT = 'risklens-canonical-json-v1'


def canonical(value):
    """Project-specific JSON format, not RFC 8785; floats use Python repr."""
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf-8')


def sha256(value: bytes):
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True)
class InputSnapshot:
    files: Mapping[str, bytes]

    def __post_init__(self):
        if set(self.files) != set(INPUT_FILES):
            raise ValueError('snapshot must contain exactly the six input files')
        if not all(isinstance(v, bytes) for v in self.files.values()):
            raise ValueError('snapshot inputs must be immutable bytes')
        object.__setattr__(self, 'files', MappingProxyType(dict(self.files)))

    @classmethod
    def capture(cls, directory: Path):
        # Each path is read once. This is a captured set, not an atomic filesystem
        # transaction across six files. Validation applies to the captured set.
        return cls({name: (directory/name).read_bytes() for name in INPUT_FILES})

    def payload(self):
        return {'format': HASH_FORMAT, 'files': {
            name: {'base64': base64.b64encode(self.files[name]).decode('ascii'),
                   'sha256': sha256(self.files[name]), 'size': len(self.files[name])}
            for name in INPUT_FILES}}

    @property
    def content_hash(self):
        return sha256(canonical(self.payload()))

    def validated_dataset(self):
        # Reuse Stage 1 rules unchanged; temporary files come only from this capture.
        with TemporaryDirectory(prefix='risklens-capture-') as folder:
            root = Path(folder)
            for name, value in self.files.items():
                (root/name).write_bytes(value)
            return load_dataset(root)

    @classmethod
    def restore(cls, payload, expected_hash):
        if sha256(canonical(payload)) != expected_hash:
            raise ValueError('snapshot content hash mismatch')
        if payload.get('format') != HASH_FORMAT or set(payload.get('files', {})) != set(INPUT_FILES):
            raise ValueError('unsupported or incomplete snapshot format')
        files = {}
        for name in INPUT_FILES:
            item = payload['files'][name]
            try:
                value = base64.b64decode(item['base64'], validate=True)
            except (ValueError, KeyError) as exc:
                raise ValueError('invalid encoded snapshot file') from exc
            if len(value) != item['size'] or sha256(value) != item['sha256']:
                raise ValueError(f'input integrity failure: {name}')
            files[name] = value
        return cls(files)
