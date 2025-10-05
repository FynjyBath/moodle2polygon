#!/usr/bin/env python3
"""Utility for migrating Moodle CodeRunner tasks into Polygon problems.

The script expects an XML export produced by Moodle in the format used by
CodeRunner quizzes.  For every task contained in the export a new Polygon
problem is created and fully configured (statement, tests, checker and model
solution).  The script prints the list of created problem identifiers to
stdout upon success.

Usage:
    python moodle2polygon.py path/to/moodle_export.xml [--config config.ini]

The configuration file must contain Polygon API credentials.  See the
accompanying README for details on the configuration format.
"""

from __future__ import annotations

import argparse
import configparser
import dataclasses
import hashlib
import json
import pathlib
import random
import sys
import time
import typing as t
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html import unescape
import re
import unicodedata


API_BASE_URL = "https://polygon.codeforces.com/api"


def _now() -> int:
    return int(time.time())


def _bool(value: bool) -> str:
    return "true" if value else "false"


@dataclasses.dataclass
class TestCase:
    index: int
    input_data: str
    output_data: str
    use_in_statements: bool


@dataclasses.dataclass
class MoodleTask:
    name: str
    legend: str
    input_format: str
    output_format: str
    solution: str
    tests: list[TestCase]


class PolygonAPIError(RuntimeError):
    pass


class PolygonAPI:
    def __init__(self, api_url: str, api_key: str, api_secret: str) -> None:
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret

    def request(self, method: str, params: dict[str, t.Any] | None = None) -> t.Any:
        params = dict(params or {})
        params["apiKey"] = self.api_key
        params["time"] = str(_now())

        params = {k: self._stringify_value(v) for k, v in params.items()}
        signature = self._build_signature(method, params)
        params["apiSig"] = signature

        encoded = urllib.parse.urlencode(params).encode("utf-8")
        url = f"{self.api_url}/{method}"
        request = urllib.request.Request(url, data=encoded)

        try:
            with urllib.request.urlopen(request) as response:
                payload = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:  # pragma: no cover - network errors
            message = f"HTTP error {exc.code}: {exc.reason}"
            try:
                error_payload = exc.read().decode("utf-8", errors="replace")
            except Exception:  # pragma: no cover - I/O decoding issues
                error_payload = ""
            if error_payload:
                try:
                    data = json.loads(error_payload)
                except json.JSONDecodeError:
                    message = f"{message}. Response: {error_payload.strip()}"
                else:
                    comment = data.get("comment") if isinstance(data, dict) else None
                    if comment:
                        message = f"{message}. {comment}"
            raise PolygonAPIError(message) from exc
        except urllib.error.URLError as exc:  # pragma: no cover - network errors
            raise PolygonAPIError(f"Network error: {exc.reason}") from exc

        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise PolygonAPIError(f"Failed to decode response: {payload!r}") from exc

        if data.get("status") != "OK":
            raise PolygonAPIError(data.get("comment", "Unknown API error"))
        return data.get("result")

    def _build_signature(self, method: str, params: dict[str, str]) -> str:
        rand = "".join(random.choices("0123456789abcdef", k=6))
        sorted_items = sorted(params.items(), key=lambda item: (item[0], item[1]))
        query = "&".join(f"{key}={value}" for key, value in sorted_items)
        signature_base = f"{rand}/{method}?{query}#{self.api_secret}"
        digest = hashlib.sha512(signature_base.encode("utf-8")).hexdigest()
        return rand + digest

    @staticmethod
    def _stringify_value(value: t.Any) -> str:
        if isinstance(value, bool):
            return _bool(value)
        return str(value)


def parse_config(path: str) -> tuple[str, str, str]:
    parser = configparser.ConfigParser()
    with open(path, "r", encoding="utf-8") as config_file:
        parser.read_file(config_file)

    if "polygon" not in parser:
        raise ValueError("Configuration file must contain [polygon] section")

    section = parser["polygon"]
    api_key = section.get("key")
    api_secret = section.get("secret")
    api_url = section.get("api_url", API_BASE_URL)

    if not api_key or not api_secret:
        raise ValueError("Polygon API key and secret must be provided in config")
    return api_url, api_key, api_secret


def extract_text_sections(html: str) -> tuple[str, str, str]:
    html = html.replace("\xa0", " ").replace("&nbsp;", " ")
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    html = re.sub(r"(?i)</(p|div|h4|h5)>", "\n", html)
    html = re.sub(r"(?i)<(p|div|h4|h5)[^>]*>", "", html)
    html = re.sub(r"(?i)</?(span|strong|b|i)[^>]*>", "", html)

    text = unescape(html)
    text = re.sub(r"<[^>]+>", "", text)

    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]

    legend_lines: list[str] = []
    input_lines: list[str] = []
    output_lines: list[str] = []
    current = "legend"
    for line in lines:
        normalized = line.lower()
        if normalized.startswith("вход") and "дан" in normalized:
            current = "input"
            continue
        if normalized.startswith("input"):
            current = "input"
            continue
        if normalized.startswith("выход") and "дан" in normalized:
            current = "output"
            continue
        if normalized.startswith("output"):
            current = "output"
            continue

        if current == "legend":
            legend_lines.append(line)
        elif current == "input":
            input_lines.append(line)
        else:
            output_lines.append(line)

    legend = "\n\n".join(legend_lines) if legend_lines else ""
    input_format = "\n".join(input_lines) if input_lines else "Входные данные отсутствуют"
    output_format = "\n".join(output_lines) if output_lines else "Выходные данные отсутствуют"
    return legend, input_format, output_format


def _normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def _extract_first_word(value: str) -> str:
    stripped = value.lstrip()
    if not stripped:
        return ""
    parts = stripped.split(None, 1)
    return parts[0] if parts else ""


def _is_integer_token(token: str) -> bool:
    return bool(re.fullmatch(r"[+-]?\d+", token))


def _is_float_token(token: str) -> bool:
    if not token or _is_integer_token(token):
        return False
    try:
        float(token)
    except ValueError:
        return False
    return any(ch.isdigit() for ch in token) and any(ch in ".eE" for ch in token)


def _select_checker(task: MoodleTask) -> str:
    if not task.tests:
        return "std::lcmp.cpp"

    first_word = _extract_first_word(task.tests[0].output_data)
    if not first_word:
        return "std::lcmp.cpp"
    if _is_integer_token(first_word):
        return "std::ncmp.cpp"
    if _is_float_token(first_word):
        return "std::rcmp9.cpp"
    return "std::lcmp.cpp"


def _strip_redundant_title(legend: str, title: str) -> str:
    if not legend:
        return legend

    sections = legend.split("\n\n")
    if not sections:
        return legend

    normalized_title = _normalize_whitespace(title).lower()
    normalized_first = _normalize_whitespace(sections[0]).lower()
    if normalized_first != normalized_title:
        return legend

    sections = sections[1:]
    while sections and not sections[0].strip():
        sections = sections[1:]
    return "\n\n".join(sections)


def parse_moodle_xml(path: str) -> tuple[str, list[MoodleTask]]:
    tree = ET.parse(path)
    root = tree.getroot()

    contest_name = ""
    tasks: list[MoodleTask] = []

    for question in root.findall("question"):
        qtype = question.get("type", "")
        if qtype == "category" and not contest_name:
            category = question.find("category")
            if category is not None:
                text_node = category.find("text")
                if text_node is not None and text_node.text:
                    contest_name = text_node.text.strip().split("/")[-1]
            continue

        if qtype != "coderunner":
            continue

        name_node = question.find("name/text")
        questiontext_node = question.find("questiontext/text")
        answer_node = question.find("answer")

        if name_node is None or questiontext_node is None or answer_node is None:
            raise ValueError("Malformed question entry in Moodle export")

        legend, input_format, output_format = extract_text_sections(questiontext_node.text or "")

        name = (name_node.text or "Unnamed task").strip()
        legend = _strip_redundant_title(legend, name)

        tests: list[TestCase] = []
        for idx, testcase in enumerate(question.findall("testcases/testcase"), start=1):
            stdin_node = testcase.find("stdin/text")
            expected_node = testcase.find("expected/text")
            if stdin_node is None or expected_node is None:
                continue
            use_example = testcase.get("useasexample", "0") == "1"
            input_data = stdin_node.text or ""
            output_data = expected_node.text or ""
            tests.append(TestCase(idx, input_data, output_data, use_example))

        tasks.append(
            MoodleTask(
                name=name,
                legend=legend,
                input_format=input_format,
                output_format=output_format,
                solution=(answer_node.text or "").strip(),
                tests=tests,
            )
        )

    if not contest_name:
        contest_name = pathlib.Path(path).stem

    return contest_name or "Moodle Contest", tasks


def slugify(value: str, fallback: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_text = ascii_text.lower()
    ascii_text = re.sub(r"[^a-z0-9]+", "-", ascii_text)
    ascii_text = ascii_text.strip("-")
    ascii_text = re.sub(r"-+", "-", ascii_text)
    return ascii_text or fallback


def create_polygon_problem(api: PolygonAPI, problem_code: str, task: MoodleTask) -> int:
    problem = api.request("problem.create", {"name": problem_code})
    if isinstance(problem, dict) and "id" in problem:
        problem_id = int(problem["id"])
    elif isinstance(problem, list) and problem and isinstance(problem[0], dict) and "id" in problem[0]:
        problem_id = int(problem[0]["id"])
    else:
        raise PolygonAPIError("Unexpected response from problem.create")

    api.request(
        "problem.updateInfo",
        {
            "problemId": problem_id,
            "inputFile": "stdin",
            "outputFile": "stdout",
            "timeLimit": 2000,
            "memoryLimit": 256,
            "interactive": False,
        },
    )

    api.request(
        "problem.saveStatement",
        {
            "problemId": problem_id,
            "lang": "russian",
            "name": task.name,
            "legend": task.legend or task.name,
            "input": task.input_format,
            "output": task.output_format,
        },
    )

    checker = _select_checker(task)
    api.request(
        "problem.setChecker", {"problemId": problem_id, "checker": checker}
    )

    api.request(
        "problem.saveSolution",
        {
            "problemId": problem_id,
            "name": "solution.py",
            "file": task.solution,
            "sourceType": "python.3",
            "tag": "MA",
        },
    )

    for test in task.tests:
        params = {
            "problemId": problem_id,
            "testset": "tests",
            "testIndex": test.index,
            "testInput": test.input_data,
            "testAnswer": test.output_data,
        }
        if test.use_in_statements:
            params.update(
                {
                    "testUseInStatements": True,
                    "testInputForStatements": test.input_data,
                    "testOutputForStatements": test.output_data,
                }
            )
        api.request("problem.saveTest", params)

    api.request("problem.commitChanges", {"problemId": problem_id, "minorChanges": False})
    api.request(
        "problem.buildPackage",
        {"problemId": problem_id, "full": True, "verify": True},
    )
    wait_for_package(api, problem_id)

    return problem_id


def wait_for_package(api: PolygonAPI, problem_id: int, timeout: int = 300) -> None:
    deadline = _now() + timeout
    while _now() < deadline:
        packages = api.request("problem.packages", {"problemId": problem_id})
        if not packages:
            time.sleep(2)
            continue
        latest = max(packages, key=lambda pkg: pkg.get("creationTimeSeconds", 0))
        state = latest.get("state")
        if state == "READY":
            return
        if state == "FAILED":
            raise PolygonAPIError(f"Package build failed for problem {problem_id}")
        time.sleep(2)
    raise PolygonAPIError(f"Timeout while waiting for package build for problem {problem_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Moodle CodeRunner tasks into Polygon")
    parser.add_argument("xml_file", help="Path to Moodle XML export")
    parser.add_argument(
        "--config",
        dest="config",
        default="polygon_config.ini",
        help="Path to configuration file with Polygon credentials",
    )
    args = parser.parse_args()

    try:
        api_url, api_key, api_secret = parse_config(args.config)
    except Exception as exc:  # pragma: no cover - configuration errors
        print(f"Failed to read configuration: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        contest_name, tasks = parse_moodle_xml(args.xml_file)
    except Exception as exc:  # pragma: no cover - parsing errors
        print(f"Failed to parse Moodle export: {exc}", file=sys.stderr)
        sys.exit(1)

    if not tasks:
        print("No CodeRunner tasks found in the provided XML.", file=sys.stderr)
        sys.exit(1)

    api = PolygonAPI(api_url, api_key, api_secret)
    contest_slug = slugify(contest_name, "contest")
    created_ids: list[int] = []

    for index, task in enumerate(tasks, start=1):
        problem_code = f"{contest_slug}-{index:02d}"
        try:
            problem_id = create_polygon_problem(api, problem_code, task)
        except Exception as exc:  # pragma: no cover - network errors
            print(f"Failed to create problem '{task.name}': {exc}", file=sys.stderr)
            sys.exit(1)
        created_ids.append(problem_id)

    for pid in created_ids:
        print(pid)


if __name__ == "__main__":
    main()
