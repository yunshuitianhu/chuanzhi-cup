import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from scripts.secret_scan import is_known_text_hash, metadata_hash_spans

HASH = '0123456789abcdef' * 4
DELIVERY_HASH = '7577e57813350d10281144d9c1751e37d2e92a16c353364681b334192a3602e2'
ROOT = Path(__file__).resolve().parents[1]


class SecretMetadataTests(unittest.TestCase):
    def raw(self, value):
        return json.dumps(value, ensure_ascii=False).encode('utf-8')

    def test_declared_hash_and_maps_in_validation_metadata(self):
        raw = self.raw({'中文':'前缀','dataset_sha256':HASH,'source_sha256':{'a.py':HASH}})
        spans = metadata_hash_spans('docs/validation/check.json',raw)
        self.assertEqual(len(spans),2)
        self.assertTrue(all(raw[a:b].decode()==HASH for a,b in spans))

    def test_same_value_in_model_body_is_not_exempt(self):
        raw = self.raw({'dataset_sha256':HASH,'answer':HASH})
        spans = metadata_hash_spans('evaluation/safety/results/a.json',raw)
        self.assertEqual(len(spans),1)
        self.assertNotIn((raw.rindex(HASH.encode()),raw.rindex(HASH.encode())+64),spans)

    def test_config_keys_are_not_hash_metadata(self):
        self.assertFalse(metadata_hash_spans('docs/validation/a.json',self.raw({'api_key':HASH})))
        self.assertFalse(metadata_hash_spans('config.json',self.raw({'sha256':HASH})))
        self.assertFalse(metadata_hash_spans('docs/validation/a.json',self.raw({'answer':{'sha256':HASH}})))
        self.assertFalse(metadata_hash_spans('docs/validation/a.json',self.raw({'source_sha256':{'api_key':HASH}})))

    def test_32_character_values_are_never_exempt(self):
        self.assertFalse(metadata_hash_spans('docs/validation/a.json',self.raw({'sha256':HASH[:32]})))

    def test_sample_manifest_sha256_is_metadata(self):
        raw=self.raw({'samples':[{'id':'S01','sha256':HASH}]})
        self.assertEqual(len(metadata_hash_spans('data/samples/manifest.json',raw)),1)

    def test_vision_evidence_hashes_are_metadata(self):
        raw=self.raw({'tests':[{'vision':{'images':[{'sha256':HASH,
            'asset_id':HASH[:32],'asset_revision':HASH,'video_revision':HASH}]}}]})
        self.assertEqual(len(metadata_hash_spans('docs/validation/a.json',raw)),3)
        self.assertEqual(len(metadata_hash_spans('docs/validation/a.json',raw,width=32)),1)

    def test_asset_id_outside_vision_is_not_exempt(self):
        raw=self.raw({'asset_id':HASH[:32]})
        self.assertFalse(metadata_hash_spans('docs/validation/a.json',raw,width=32))
        self.assertFalse(metadata_hash_spans('data/samples/manifest.json',raw,width=32))

    def test_image_sha256_metadata_is_exempt_but_answer_text_is_not(self):
        raw=self.raw({'tests':[{'product_chat':{'image_sha256':HASH}},
                               {'answer':'data_version: '+HASH}]})
        self.assertEqual(len(metadata_hash_spans('docs/validation/a.json',raw)),1)

    def test_only_known_text_hash_is_allowlisted(self):
        self.assertTrue(is_known_text_hash('c5acfb7e952f3bdb0e4c9c01a57b2a12d9fca1f3d53a961fbceaf5139dcb7c1b'))
        self.assertFalse(is_known_text_hash(HASH))

    def test_verified_delivery_checksum_is_recognized_exactly(self):
        self.assertTrue(is_known_text_hash(DELIVERY_HASH))
        for other in (DELIVERY_HASH[:32], DELIVERY_HASH[:-1],
                      DELIVERY_HASH + '0', DELIVERY_HASH[:-1] + '3'):
            with self.subTest(value=other):
                self.assertFalse(is_known_text_hash(other))

    def test_escaped_json_inside_model_text_is_not_metadata(self):
        raw = self.raw({'answer':json.dumps({'sha256':HASH})})
        self.assertFalse(metadata_hash_spans('evaluation/safety/results/a.json',raw))

    def test_malformed_json_never_creates_exemptions(self):
        self.assertFalse(metadata_hash_spans('docs/validation/a.json',b'{"sha256":"'+HASH.encode()+b'",}'))


class SecretScanCliTests(unittest.TestCase):
    """Run the real scanner against staged files in an isolated Git fixture."""

    def scan(self, files, env_file=None):
        with tempfile.TemporaryDirectory(prefix='secret-scan-') as folder:
            root = Path(folder)
            fixtures = {
                '.gitignore': '.env\n.mcp.json\n',
                'scripts/__init__.py': '',
                'water_quality_mcp/__init__.py': '',
                'water_quality_mcp/src/__init__.py': '',
                'water_quality_mcp/src/water_quality_mcp/__init__.py': '',
                **files,
            }
            for relative, text in fixtures.items():
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding='utf-8')
            for relative in ('scripts/secret_scan.py', 'tests/密钥泄漏检查.py',
                             'water_quality_mcp/src/water_quality_mcp/config.py'):
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, target)
            if env_file is not None:
                (root / '.env').write_text(env_file, encoding='utf-8')
            # Never inherit a caller's worktree/index or load their .env.
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith('GIT_')}
            env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8',
                       PYTHONDONTWRITEBYTECODE='1')
            for command in (['git', 'init', '-q'], ['git', 'add', '.']):
                subprocess.run(command, cwd=root, env=env, check=True,
                               capture_output=True, timeout=30)
            return subprocess.run([sys.executable, 'tests/密钥泄漏检查.py'],
                                  cwd=root, env=env, capture_output=True,
                                  text=True, encoding='utf-8', timeout=30)

    def test_delivery_json_and_markdown_do_not_fail_the_gate(self):
        result = self.scan({
            'docs/validation/delivery_r2_20261028.json':
                json.dumps({'package': {'sha256': DELIVERY_HASH}}),
            'docs/交付说明.md': 'ZIP SHA-256: `' + DELIVERY_HASH + '`',
        })
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('9 通过, 0 失败', result.stdout)

    def test_other_hex_and_prefixed_tokens_still_fail(self):
        hex64 = hashlib.sha256(b'fake-credential-fixture').hexdigest()
        hex32 = hashlib.md5(b'fake-map-token-fixture').hexdigest()
        token = 'sk-' + 'fixturecredential123456789'
        result = self.scan({
            'config.py': 'api_key = "' + hex64 + '"',
            'docs/answer.md': hex32 + '\n' + token,
        })
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        for label in ('32位hex', '64位hex', 'sk-密钥'):
            self.assertIn('疑似 ' + label, result.stdout)
        self.assertNotIn(hex64, result.stdout)
        self.assertNotIn(hex32, result.stdout)
        self.assertNotIn(token, result.stdout)

    def test_env_value_scan_overrides_the_checksum_exception(self):
        result = self.scan({'docs/checksum.md': DELIVERY_HASH},
                           'STARCLOUD_API_KEY=' + DELIVERY_HASH + '\n')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('含 STARCLOUD_API_KEY', result.stdout)
        self.assertNotIn(DELIVERY_HASH, result.stdout)


if __name__ == '__main__':
    unittest.main()
