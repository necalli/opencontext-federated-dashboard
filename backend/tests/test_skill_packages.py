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

    def test_resolve_rental_dashboard_skill(self) -> None:
        context = self.registry.resolve_for_message(
            "Ingest this Airbnb listing with lite reviews and then show captured reviews."
        )
        self.assertIn("rental_dashboard_ops", context["selected_skill_ids"])
        self.assertIn("ingest_listing_url", context["allowed_tool_patterns"])
        self.assertIn("get_listing_reviews", context["allowed_tool_patterns"])
        self.assertNotIn("analysis_sql", context["selected_skill_ids"])

    def test_rental_prompt_does_not_trigger_civic_sql(self) -> None:
        context = self.registry.resolve_for_message(
            "Compare these New York Airbnb listings and check their reviews."
        )
        self.assertIn("rental_dashboard_ops", context["selected_skill_ids"])
        self.assertNotIn("analysis_sql", context["selected_skill_ids"])

    def test_open_data_listing_prompt_does_not_trigger_rental_skill(self) -> None:
        context = self.registry.resolve_for_message(
            "Find Socrata open data listings for NYC restaurant inspections."
        )
        self.assertNotIn("rental_dashboard_ops", context["selected_skill_ids"])

    def test_sticky_followup_preserves_rental_context(self) -> None:
        context = self.registry.resolve_for_message(
            "Check status again.",
            sticky_skill_ids=["rental_dashboard_ops"],
        )
        self.assertIn("rental_dashboard_ops", context["selected_skill_ids"])
        self.assertIn("get_job", context["allowed_tool_patterns"])

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

    def test_resolve_mcp_onboarder_skill(self) -> None:
        context = self.registry.resolve_for_message(
            "Please register a new MCP server endpoint and verify MCP tools."
        )
        self.assertIn("mcp-server-onboarder", context["selected_skill_ids"])
        self.assertIn("mcp_server_onboard", context["allowed_tool_patterns"])
        self.assertIn("mcp_server_discover", context["allowed_tool_patterns"])

    def test_resolve_mcp_onboarder_for_package_style_prompt(self) -> None:
        context = self.registry.resolve_for_message("add @matchuplabs/nyc-api-mcp")
        self.assertIn("mcp-server-onboarder", context["selected_skill_ids"])
        self.assertIn("mcp_servers_list", context["allowed_tool_patterns"])

    def test_resolve_mcp_onboarder_for_recommendation_prompt(self) -> None:
        context = self.registry.resolve_for_message(
            "I want to add a new mcp server that is data or finance related. "
            "What interesting ones are available that you recommend?"
        )
        self.assertIn("mcp-server-onboarder", context["selected_skill_ids"])
        self.assertIn("mcp_server_discover", context["allowed_tool_patterns"])

    def test_resolve_rental_dashboard_ops_for_airbnb_search(self) -> None:
        context = self.registry.resolve_for_message(
            "Search rental listings in the Adirondacks NY for 2 people, pet friendly, from 8/10 to 8/14."
        )
        self.assertIn("rental_dashboard_ops", context["selected_skill_ids"])
        self.assertIn("search_airbnb_listings", context["allowed_tool_patterns"])
        self.assertIn("get_job", context["allowed_tool_patterns"])
        self.assertIn("get_jobs", context["allowed_tool_patterns"])
        self.assertIn("get_search_listings", context["allowed_tool_patterns"])


if __name__ == "__main__":
    unittest.main()
