"""Verify team configuration reaches the unified benchmark entry point."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vista.cli import main


class ConfigurationTests(unittest.TestCase):
    def test_model_is_loaded_from_explicit_env_file(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text("VISTA_MODEL=team-model\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True), patch(
                "vista.localbench.doctor", return_value=0
            ) as doctor:
                self.assertEqual(main(["doctor", "--env-file", str(env_file)]), 0)
                self.assertEqual(doctor.call_args.args[0].model, "team-model")

    def test_explicit_model_overrides_env_configuration(self):
        with patch.dict(os.environ, {"VISTA_MODEL": "default-model"}), patch(
            "vista.localbench.doctor", return_value=0
        ) as doctor:
            self.assertEqual(main(["doctor", "--model", "comparison-model"]), 0)
            self.assertEqual(doctor.call_args.args[0].model, "comparison-model")
