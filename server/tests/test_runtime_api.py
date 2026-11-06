"""Structural checks for the unified API contract; no server is required."""
from pathlib import Path
import re
import unittest

import yaml


DOCUMENT = Path(__file__).parents[1] / 'management' / 'runtime-api-v1.yaml'
METHODS = {'get', 'post', 'put', 'patch', 'delete'}


class RuntimeApiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = yaml.safe_load(DOCUMENT.read_text(encoding='utf-8'))

    def test_document_has_versioned_paths_and_three_auth_domains(self):
        self.assertEqual(self.document['openapi'], '3.1.0')
        self.assertTrue(all(path.startswith('/v1/') or path == '/health/ready'
                            for path in self.document['paths']))
        schemes = self.document['components']['securitySchemes']
        self.assertEqual({'userBearer', 'deviceBearer', 'wechatOpenId'}, set(schemes))

    def test_every_operation_has_a_unique_operation_id(self):
        operation_ids = []
        for methods in self.document['paths'].values():
            for method, operation in methods.items():
                if method in METHODS:
                    self.assertIn('operationId', operation)
                    operation_ids.append(operation['operationId'])
        self.assertEqual(len(operation_ids), len(set(operation_ids)))

    def test_every_local_reference_resolves(self):
        references = []

        def walk(value):
            if isinstance(value, dict):
                if '$ref' in value:
                    references.append(value['$ref'])
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(self.document)
        self.assertTrue(references)
        for reference in references:
            with self.subTest(reference=reference):
                self.assertTrue(reference.startswith('#/'))
                value = self.document
                for part in reference[2:].split('/'):
                    value = value[part.replace('~1', '/').replace('~0', '~')]
                self.assertIsInstance(value, dict)

    def test_path_parameters_are_declared_and_required(self):
        for path, methods in self.document['paths'].items():
            placeholders = set(re.findall(r'\{([^}]+)\}', path))
            declared = set()
            for parameter in methods.get('parameters', []):
                if parameter.get('in') == 'path':
                    self.assertTrue(parameter.get('required'))
                    declared.add(parameter['name'])
            for method, operation in methods.items():
                if method not in METHODS:
                    continue
                operation_parameters = set()
                for parameter in operation.get('parameters', []):
                    if '$ref' in parameter:
                        parameter = self.document['components']['parameters'][parameter['$ref'].rsplit('/', 1)[-1]]
                    if parameter.get('in') == 'path':
                        self.assertTrue(parameter.get('required'))
                        operation_parameters.add(parameter['name'])
                self.assertEqual(placeholders, declared | operation_parameters, path)


if __name__ == '__main__':
    unittest.main()
