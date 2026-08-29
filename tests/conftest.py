"""Runs `async def test_*` functions with asyncio.run(), so the async
translation-manager tests work with plain pytest -- no pytest-asyncio
dependency needed on top of what this project already uses for testing.
"""
import asyncio
import inspect
import sys
import types

# mega.py (a git-forked dependency, see requirements.txt) isn't relevant
# to anything under test here and pulls in heavy/awkward install
# machinery; stub it out so cogs/admin.py (which imports `from mega
# import Mega` at module scope) can be collected without it.
if "mega" not in sys.modules:
    _mega_stub = types.ModuleType("mega")
    _mega_stub.Mega = type("Mega", (), {})
    sys.modules["mega"] = _mega_stub


def pytest_pyfunc_call(pyfuncitem):
    test_function = pyfuncitem.obj
    if inspect.iscoroutinefunction(test_function):
        argnames = pyfuncitem._fixtureinfo.argnames
        kwargs = {name: pyfuncitem.funcargs[name] for name in argnames}
        asyncio.run(test_function(**kwargs))
        return True
    return None


def pytest_configure(config):
    config.addinivalue_line("markers", "asyncio: run this async test via asyncio.run()")
