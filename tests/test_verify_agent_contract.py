from __future__ import annotations

import unittest

from scripts import verify_agent_contract


class AgentContractTests(unittest.TestCase):
    def test_current_repo_passes(self) -> None:
        self.assertEqual(verify_agent_contract.main(), 0)


if __name__ == "__main__":
    unittest.main()
