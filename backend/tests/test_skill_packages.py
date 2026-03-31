import unittest

from services.skill_packages import SkillPackageRegistry, tool_allowed


class SkillPackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = SkillPackageRegistry()

    def test_resolve_dataset_discovery_skill(self) -> None:
        context = self.registry.resolve_for_message(
            "Search Boston public safety datasets and summarize them."
        )
        self.assertIn("dataset_discovery", context["selected_skill_ids"])
        self.assertGreaterEqual(len(context["allowed_tool_patterns"]), 1)
        self.assertIn("ckan__search_datasets", context["allowed_tool_names"])

    def test_resolve_sql_analysis_skill(self) -> None:
        context = self.registry.resolve_for_message(
            "Run an aggregate SQL query with group by for incidents."
        )
        self.assertIn("analysis_sql", context["selected_skill_ids"])
        self.assertIn("ckan__execute_sql", context["allowed_tool_patterns"])

    def test_empty_message_has_no_skill_scope(self) -> None:
        context = self.registry.resolve_for_message("   ")
        self.assertEqual(context["selected_skill_ids"], [])
        self.assertEqual(context["allowed_tool_patterns"], [])
        self.assertEqual(context["system_prompt_addendum"], "")

    def test_resolve_visualization_skill(self) -> None:
        context = self.registry.resolve_for_message(
            "Visualize monthly NYC 311 complaint trends with a chart."
        )
        self.assertIn("visualization-expert", context["selected_skill_ids"])
        self.assertIn("create_visualization", context["allowed_tool_patterns"])

    def test_tool_allowed_matches_patterns(self) -> None:
        self.assertTrue(tool_allowed("ckan__search_datasets", ["ckan__search_*"]))
        self.assertFalse(tool_allowed("ckan__execute_sql", ["ckan__search_*"]))
        self.assertTrue(tool_allowed("ckan__execute_sql", []))


if __name__ == "__main__":
    unittest.main()
