"""The simplified title row preserves existing plan identity and phone selection."""
from html.parser import HTMLParser
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent


class Nodes(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.stack = []
        self.ids = {}
        self.nodes = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        node = {"tag": tag, "attrs": dict(attrs), "parents": list(self.stack)}
        self.nodes.append(node)
        if "id" in node["attrs"]:
            self.ids.setdefault(node["attrs"]["id"], []).append(node)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                self.stack = self.stack[:index]
                return

    def node(self, identity):
        return self.ids[identity][0]


def has_class(node, value):
    return value in node["attrs"].get("class", "").split()


class WorkspaceHeaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dom = Nodes((ROOT / "templates/workspace.html").read_text(encoding="utf-8"))

    def test_plan_management_controls_remain_unique_and_hidden_without_losing_data_hooks(self):
        for identity in ["board-select", "new-board", "copy-board", "share-board", "archive-board", "show-archived", "board-state"]:
            self.assertEqual(len(self.dom.ids[identity]), 1, identity)
            bar = next(node for node in self.dom.node(identity)["parents"] if has_class(node, "board-bar"))
            self.assertIn("hidden", bar["attrs"])
            self.assertIn("inert", bar["attrs"])
            self.assertEqual(bar["attrs"].get("aria-hidden"), "true")

    def test_layout_picker_is_available_once_in_symbol_title_actions(self):
        self.assertEqual(len(self.dom.ids["layout-picker"]), 1)
        parents = self.dom.node("layout-picker")["parents"]
        self.assertTrue(any(has_class(node, "market-strip-actions") for node in parents))
        self.assertTrue(any(has_class(node, "market-strip") for node in parents))
        self.assertFalse(any(has_class(node, "board-bar") or "hidden" in node["attrs"] for node in parents if node["attrs"].get("id") != "workspace"))
        self.assertEqual(len(self.dom.ids["layout-menu"]), 1)

    def test_phone_symbol_selector_is_not_trapped_in_hidden_management_row(self):
        self.assertEqual(len(self.dom.ids["symbol"]), 1)
        parents = self.dom.node("symbol")["parents"]
        self.assertTrue(any(has_class(node, "mobile-symbol-control") for node in parents))
        self.assertTrue(any(has_class(node, "market-strip") for node in parents))
        self.assertFalse(any(has_class(node, "board-bar") for node in parents))

    def test_entry_stop_inspector_and_drawings_keep_original_nodes(self):
        for identity in ["quick-plan-toggle", "objects-toggle", "ticket-entry", "ticket-stop", "workspace-dock", "chart-day", "chart-m30", "chart-m3"]:
            self.assertEqual(len(self.dom.ids[identity]), 1, identity)
            self.assertFalse(any(has_class(node, "board-bar") for node in self.dom.node(identity)["parents"]))


if __name__ == "__main__":
    unittest.main()
