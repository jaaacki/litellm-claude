import os
import unittest

import config


class ConfigPathResolutionTests(unittest.TestCase):
    def test_live_env_path_points_to_repo_root(self):
        self.assertEqual(
            os.path.realpath(os.path.join(os.path.dirname(config.DIR), ".env")),
            os.path.realpath(config.ENV_PATH),
        )


if __name__ == "__main__":
    unittest.main()
