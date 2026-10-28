import json
import unittest
from scripts.secret_scan import is_known_text_hash, metadata_hash_spans

HASH = '0123456789abcdef' * 4


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

    def test_escaped_json_inside_model_text_is_not_metadata(self):
        raw = self.raw({'answer':json.dumps({'sha256':HASH})})
        self.assertFalse(metadata_hash_spans('evaluation/safety/results/a.json',raw))

    def test_malformed_json_never_creates_exemptions(self):
        self.assertFalse(metadata_hash_spans('docs/validation/a.json',b'{"sha256":"'+HASH.encode()+b'",}'))


if __name__ == '__main__':
    unittest.main()
