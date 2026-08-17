"""Every database reader must have a live caller.

THE BUG THIS CATCHES, THREE TIMES OVER

    feedback_db.list_feedback()     people left feedback for weeks; no
                                    screen ever showed it
    notes_db.list_updates()         a generated investor update was
                                    reachable only by the redirect right
                                    after generating it
    the notetaker itself            fully built, fully tested, and linked
                                    from nowhere in the navigation

Every one of them was correct code. Every one passed its own unit tests.
Every one was invisible to a real person using the app, because the
function that would have surfaced it was never called. The fix for the
first case added a test scoped to feedback alone, which is why it could
not catch the second or the third.

So this is the general form: a reader nothing calls is a feature nothing
shows.

WHAT COUNTS AS A READER

Module-level functions in tools/*_db.py whose names begin list_, get_,
fetch_, find_, count_, search_, load_ or read_. Private helpers (leading
underscore) are excluded -- they are implementation, and their caller is
by definition inside the module.

WHAT COUNTS AS A CALLER

Any reference outside the defining module, in application code or in a
template. Tests do NOT count. A reader exercised only by its own unit
test is precisely the failure above: proof the function works, no proof
anything uses it.

This is a naming-convention sweep, not a call graph. It cannot see a
reader invoked through getattr or a dispatch table, and it does not try
to. Both of those are rare here and both would be worth a comment at the
call site anyway.

CALLS ARE FOUND BY PARSING, NOT BY GREP, AND THAT MATTERS

The first version of this file matched `name(` as text. It reported
list_updates() as having a caller when its only remaining mentions were
two prose comments -- written, as it happens, to explain that the
function had once been dead. A checker that a comment can satisfy is
worse than no checker, because it reports safety it has not established.

Python files are parsed and only genuine ast.Call nodes count, so
comments and docstrings cannot vouch for anything. Templates are matched
as text with Jinja and HTML comments stripped first.

THE ALLOWLIST

Entries are exempt only with a stated reason. A reader that is dead
because nobody got to the screen yet is a different thing from one that
is dead because it is a diagnostic, and the difference belongs in
writing. Stale entries fail too: once something acquires a real caller it
has to leave the list, or the list slowly stops meaning anything.
"""

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

READER_PREFIXES = ("list_", "get_", "fetch_", "find_", "count_",
                   "search_", "load_", "read_")

# (module_stem, function) -> why it is allowed to have no caller.
ALLOWED_DEAD = {
    ("investor_report_db", "get_investor"):
        "Symmetric CRUD accessor written beside list_investors() and "
        "delete_investor(). Nothing needs one investor by id -- the "
        "waterfall works from the full list. Unlike list_feedback() and "
        "list_updates(), no feature is hidden behind it: there is no "
        "single-investor screen that silently fails to exist. Delete it "
        "or use it, but it is not concealing anything.",
    ("site_dd_db", "list_bank_items"):
        "A diagnostic, not an entry point. Its own docstring says it "
        "reads the table rather than the module 'so a caller can see what "
        "the database actually holds' -- it exists so tests can assert "
        "the mirrored bank matches the code. Test-only is its purpose, "
        "not a symptom.",
}


def reader_functions():
    """Every public reader in tools/*_db.py, as (module, name, lineno)."""
    out = []
    for path in sorted(ROOT.glob("tools/*_db.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name.startswith("_"):
                continue
            if node.name.startswith(READER_PREFIXES):
                out.append((path.stem, node.name, node.lineno))
    return out


def production_sources():
    """Application code and templates. Deliberately excludes tests/."""
    files = []
    for path in ROOT.rglob("*.py"):
        parts = path.parts
        if "tests" in parts or "__pycache__" in parts or ".git" in parts:
            continue
        if path.name.startswith("test_"):
            continue
        files.append(path)
    files.extend(ROOT.rglob("templates/**/*.html"))
    return files


JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.S)
HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)


def called_names(path):
    """Every function name genuinely called in one file.

    Python is parsed, so a name appearing in a comment or a docstring
    contributes nothing -- which is the entire reason this is an AST walk
    and not a regex. Templates are text with their comments removed.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".py":
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return set()
        names = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute):
                names.add(func.attr)
            elif isinstance(func, ast.Name):
                names.add(func.id)
        return names
    stripped = HTML_COMMENT.sub(" ", JINJA_COMMENT.sub(" ", text))
    return set(re.findall(r"\b(\w+)\s*\(", stripped))


class ReaderEnumerationTests(unittest.TestCase):
    """The sweep has to actually sweep something.

    A convention-based check that silently matches nothing passes
    forever. If tools/*_db.py is ever renamed, this fails rather than
    quietly stopping work.
    """

    def test_it_finds_the_db_modules(self):
        self.assertGreaterEqual(len(list(ROOT.glob("tools/*_db.py"))), 5)

    def test_it_finds_a_plausible_number_of_readers(self):
        self.assertGreaterEqual(len(reader_functions()), 40)

    def test_it_reads_real_application_files(self):
        names = {p.name for p in production_sources()}
        self.assertIn("app.py", names)
        self.assertIn("investor_notes.py", names)

    def test_no_test_file_is_treated_as_a_caller(self):
        for path in production_sources():
            with self.subTest(path=str(path)):
                self.assertNotIn("tests", path.parts)
                self.assertFalse(path.name.startswith("test_"))


class EveryReaderHasACallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.calls = {p: called_names(p) for p in production_sources()}

    def callers_of(self, module, name):
        return [p for p, names in self.calls.items()
                if p.stem != module and name in names]

    def test_no_reader_is_dead(self):
        """The whole point. A reader nothing calls shows nothing."""
        dead = []
        for module, name, lineno in reader_functions():
            if (module, name) in ALLOWED_DEAD:
                continue
            if not self.callers_of(module, name):
                dead.append(f"tools/{module}.py:{lineno} {name}()")
        self.assertEqual(
            dead, [],
            "These readers have no caller outside their own module and "
            "outside tests, so whatever they return reaches no one:\n  "
            + "\n  ".join(dead)
            + "\n\nEither call it from the screen that should show it, or "
              "add it to ALLOWED_DEAD with a reason.")

    def test_the_allowlist_has_no_stale_entries(self):
        """Once something is genuinely used it must leave the list."""
        revived = [f"{m}.{n}" for (m, n) in ALLOWED_DEAD
                   if self.callers_of(m, n)]
        self.assertEqual(
            revived, [],
            "These are on ALLOWED_DEAD but now have real callers; remove "
            "them from the allowlist: " + ", ".join(revived))

    def test_the_allowlist_only_names_readers_that_exist(self):
        known = {(m, n) for m, n, _ in reader_functions()}
        missing = [f"{m}.{n}" for (m, n) in ALLOWED_DEAD if (m, n) not in known]
        self.assertEqual(
            missing, [],
            "ALLOWED_DEAD names functions that no longer exist: "
            + ", ".join(missing))

    def test_every_allowlist_entry_states_a_reason(self):
        for key, reason in ALLOWED_DEAD.items():
            with self.subTest(entry=key):
                self.assertGreater(
                    len(reason.strip()), 40,
                    f"{key} needs a real reason, not a placeholder")


class TheKnownRegressionsTests(unittest.TestCase):
    """The three that motivated this file, asserted directly.

    Kept separate from the sweep so that if the sweep is ever narrowed,
    these three still fail on their own terms.
    """

    @classmethod
    def setUpClass(cls):
        cls.calls = {p: called_names(p) for p in production_sources()}

    def has_caller(self, module, name):
        return any(p.stem != module and name in names
                   for p, names in self.calls.items())

    def test_feedback_is_shown_somewhere(self):
        self.assertTrue(self.has_caller("feedback_db", "list_feedback"))

    def test_generated_updates_are_listed_somewhere(self):
        self.assertTrue(self.has_caller("investor_notes_db", "list_updates"))

    def test_the_notetaker_is_linked_from_the_shell(self):
        base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        self.assertIn("investor_notes.index", base)


if __name__ == "__main__":
    unittest.main()
