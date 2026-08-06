from __future__ import annotations

import unittest

from scripts.run_reducer_mutation_gate import evaluate_results, parse_results


class ReducerMutationGateTests(unittest.TestCase):
    def test_parser_and_gate_accept_only_killed_critical_mutants(self) -> None:
        patterns = ("module.first*", "module.second*")
        results = parse_results(
            """
            scripts.agent.campaign_engine.reducer.x__check_fence__mutmut_1: killed
            module.first__mutmut_1: killed
            module.second__mutmut_1: killed
            module.unselected__mutmut_1: survived
            """
        )
        receipt = evaluate_results(results, patterns)
        self.assertTrue(receipt["passed"])
        self.assertEqual(receipt["selected_mutants"], 2)
        self.assertEqual(receipt["killed_mutants"], 2)

    def test_real_mutmut_result_identifiers_match_critical_patterns(self) -> None:
        from scripts.run_reducer_mutation_gate import (
            CRITICAL_RESULT_PATTERNS,
            CRITICAL_RUN_PATTERNS,
        )

        results = {
            "scripts.agent.campaign_engine.reducer.x__check_fence__mutmut_1": "killed",
            "scripts.agent.campaign_engine.reducer.x__consume_budget__mutmut_1": "killed",
            "scripts.agent.campaign_engine.reducer.x__consume_automated__mutmut_1": "killed",
            "scripts.agent.campaign_engine.reducer.x__finish_revision__mutmut_1": "killed",
        }
        self.assertEqual(CRITICAL_RUN_PATTERNS, CRITICAL_RESULT_PATTERNS)
        self.assertTrue(
            evaluate_results(
                results,
                CRITICAL_RESULT_PATTERNS,
                equivalent_mutants={},
            )["passed"]
        )

    def test_gate_accepts_only_the_four_exact_documented_equivalents(self) -> None:
        from scripts.run_reducer_mutation_gate import EQUIVALENT_MUTANTS

        results = {
            "scripts.agent.campaign_engine.reducer.x__check_fence__mutmut_1": "killed",
            "scripts.agent.campaign_engine.reducer.x__consume_budget__mutmut_1": "killed",
            "scripts.agent.campaign_engine.reducer.x__consume_automated__mutmut_1": "killed",
            "scripts.agent.campaign_engine.reducer.x__finish_revision__mutmut_1": "killed",
            **{name: "survived" for name in EQUIVALENT_MUTANTS},
        }
        receipt = evaluate_results(results)
        self.assertTrue(receipt["passed"])
        self.assertEqual(
            set(receipt["equivalent_survivors"]),
            set(EQUIVALENT_MUTANTS),
        )
        nearby = dict(results)
        nearby[
            "scripts.agent.campaign_engine.reducer.x__consume_budget__mutmut_13"
        ] = "survived"
        self.assertFalse(evaluate_results(nearby)["passed"])

    def test_gate_rejects_survivors_missing_patterns_and_empty_results(self) -> None:
        patterns = ("module.first*", "module.second*")
        survivor = evaluate_results(
            {
                "module.first__mutmut_1": "survived",
                "module.second__mutmut_1": "killed",
            },
            patterns,
        )
        self.assertFalse(survivor["passed"])
        self.assertEqual(
            survivor["non_killed"],
            {"module.first__mutmut_1": "survived"},
        )
        missing = evaluate_results(
            {"module.first__mutmut_1": "killed"}, patterns
        )
        self.assertFalse(missing["passed"])
        self.assertEqual(missing["missing_patterns"], ["module.second*"])
        self.assertFalse(evaluate_results({}, patterns)["passed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
