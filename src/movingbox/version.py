"""The version, and nothing else.

The single place the number lives: pyproject reads it from here (hatchling's
dynamic version), the API serves it, and the pre-commit hook bumps the patch
here on every commit that does not bump it itself. In its own file because the
hook edits it with sed -- nothing else should be in reach.

0.x on purpose: one person's mid-move tool, whose surface still changes daily.
"""

__version__ = "0.2.7"
