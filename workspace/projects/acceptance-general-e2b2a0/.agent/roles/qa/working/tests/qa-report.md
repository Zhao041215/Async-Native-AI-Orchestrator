# QA Report

## Fallback Verification
- Reason: model/tool execution failed before publishing QA evidence.
- Test files written: True
- Compile check: passed
- Unit check: failed

## Raw Evidence
```json
{
  "compile": {
    "ok": true,
    "code": 0,
    "stdout": "Listing 'apps'...\nListing 'apps\\\\api'...\nCompiling 'apps\\\\api\\\\models.py'...\nCompiling 'apps\\\\api\\\\repository.py'...\nListing 'apps\\\\web'...\nListing 'tests'...\nCompiling 'tests\\\\test_operations_platform.py'...\n",
    "stderr": ""
  },
  "unit": {
    "ok": false,
    "code": 1,
    "stdout": "",
    "stderr": "E\n======================================================================\nERROR: test_operations_platform (unittest.loader._FailedTest.test_operations_platform)\n----------------------------------------------------------------------\nImportError: Failed to import test module: test_operations_platform\nTraceback (most recent call last):\n  File \"C:\\Python314\\Lib\\unittest\\loader.py\", line 426, in _find_test_path\n    module = self._get_module_from_name(name)\n  File \"C:\\Python314\\Lib\\unittest\\loader.py\", line 367, in _get_module_from_name\n    __import__(name)\n    ~~~~~~~~~~^^^^^^\n  File \"E:\\ProgramProjects\\AI_Agent\\workspace\\projects\\acceptance-general-e2b2a0\\tests\\test_operations_platform.py\", line 12, in <module>\n    from analytics import build_analytics\nModuleNotFoundError: No module named 'analytics'\n\n\n----------------------------------------------------------------------\nRan 1 test in 0.001s\n\nFAILED (errors=1)\n"
  }
}
```

## Follow-up
- Replace fallback QA with role-generated scenario coverage when the model endpoint is stable.
- Add browser smoke coverage for apps/web.
