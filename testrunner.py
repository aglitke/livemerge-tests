import sys
import os
import unittest
from functools import wraps
from nose import config
from nose import core
from nose import result

PERMUTATION_ATTR = "_permutations_"

def _getPermutation(f, args):
    @wraps(f)
    def wrapper(self):
        return f(self, *args)

    return wrapper


def _getFuncArgStr(f, args):
    # [1:] Skips self
    argNames = f.func_code.co_varnames[1:]
    return ", ".join("%s=%r" % arg for arg in zip(argNames, args))


def expandPermutations(cls):
    for attr in dir(cls):
        f = getattr(cls, attr)
        if not hasattr(f, PERMUTATION_ATTR):
            continue

        perm = getattr(f, PERMUTATION_ATTR)
        for args in perm:
            argStr = _getFuncArgStr(f, args)

            permName = "%s(%s)" % (f.func_name, argStr)
            wrapper = _getPermutation(f, args)
            wrapper.func_name = permName

            setattr(cls, permName, wrapper)

        delattr(cls, f.func_name)

    return cls


def permutations(perms):
    def wrap(func):
        setattr(func, PERMUTATION_ATTR, perms)
        return func

    return wrap


class TermColor(object):
    black = 30
    red = 31
    green = 32
    yellow = 33
    blue = 34
    magenta = 35
    cyan = 36
    white = 37


def colorWrite(stream, text, color):
    if os.isatty(stream.fileno()) or os.environ.get("NOSE_COLOR", False):
        stream.write('\x1b[%s;1m%s\x1b[0m' % (color, text))
    else:
        stream.write(text)


class VdsmTestResult(result.TextTestResult):
    def __init__(self, *args, **kwargs):
        result.TextTestResult.__init__(self, *args, **kwargs)
        self._last_case = None

    def getDescription(self, test):
        return str(test)

    def _writeResult(self, test, long_result, color, short_result, success):
        if self.showAll:
            colorWrite(self.stream, long_result, color)
            self.stream.writeln()
        elif self.dots:
            self.stream.write(short_result)
            self.stream.flush()

    def addSuccess(self, test):
        unittest.TestResult.addSuccess(self, test)
        self._writeResult(test, 'OK', TermColor.green, '.', True)

    def addFailure(self, test, err):
        unittest.TestResult.addFailure(self, test, err)
        self._writeResult(test, 'FAIL', TermColor.red, 'F', False)

    def addSkip(self, test, reason):
        # 2.7 skip compat
        from nose.plugins.skip import SkipTest
        if SkipTest in self.errorClasses:
            storage, label, isfail = self.errorClasses[SkipTest]
            storage.append((test, reason))
            self._writeResult(test, 'SKIP : %s' % reason, TermColor.blue, 'S',
                              True)

    def addError(self, test, err):
        stream = getattr(self, 'stream', None)
        ec, ev, tb = err
        try:
            exc_info = self._exc_info_to_string(err, test)
        except TypeError:
            # 2.3 compat
            exc_info = self._exc_info_to_string(err)
        for cls, (storage, label, isfail) in self.errorClasses.items():
            if result.isclass(ec) and issubclass(ec, cls):
                if isfail:
                    test.passed = False
                storage.append((test, exc_info))
                # Might get patched into a streamless result
                if stream is not None:
                    if self.showAll:
                        message = [label]
                        detail = result._exception_detail(err[1])
                        if detail:
                            message.append(detail)
                        stream.writeln(": ".join(message))
                    elif self.dots:
                        stream.write(label[:1])
                return
        self.errors.append((test, exc_info))
        test.passed = False
        if stream is not None:
            self._writeResult(test, 'ERROR', TermColor.red, 'E', False)

    def startTest(self, test):
        unittest.TestResult.startTest(self, test)
        current_case = test.test.__class__.__name__

        if self.showAll:
            if current_case != self._last_case:
                self.stream.writeln(current_case)
                self._last_case = current_case

            self.stream.write(
                '    %s' % str(test.test._testMethodName).ljust(60))
            self.stream.flush()

class VdsmTestRunner(core.TextTestRunner):
    def __init__(self, *args, **kwargs):
        core.TextTestRunner.__init__(self, *args, **kwargs)

    def _makeResult(self):
        return VdsmTestResult(self.stream,
                              self.descriptions,
                              self.verbosity,
                              self.config)

    def run(self, test):
        result_ = core.TextTestRunner.run(self, test)
        return result_


def run():
    argv = sys.argv
    stream = sys.stdout
    verbosity = 3
    testdir = os.path.dirname(os.path.abspath(__file__))

    conf = config.Config(stream=stream,
                         env=os.environ,
                         verbosity=verbosity,
                         workingDir=testdir,
                         plugins=core.DefaultPluginManager())

    runner = VdsmTestRunner(stream=conf.stream,
                            verbosity=conf.verbosity,
                            config=conf)

    sys.exit(not core.run(config=conf, testRunner=runner, argv=argv))


if __name__ == '__main__':
    run()
