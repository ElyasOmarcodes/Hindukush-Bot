"""د ډیزاین پیکج — د جیمنای پایله درې ډیزاین شوي انځورونو ته اړوي.

Design package: turns the Gemini result into three 1650×1650 news images
(Pashto, Persian, English). ``render_all`` is the single entry point.
"""

from .render import render_all

__all__ = ["render_all"]
