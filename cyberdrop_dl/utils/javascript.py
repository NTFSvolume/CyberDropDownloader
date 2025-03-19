from __future__ import annotations

import json
import re
from typing import Any

import esprima
from esprima import nodes

from cyberdrop_dl.utils.logger import log_debug

HTTPS_PLACEHOLDER = "<<SAFE_HTTPS>>"
HTTP_PLACEHOLDER = "<<SAFE_HTTP>>"
QUOTE_KEYS_REGEX = r"(\w+)\s?:", r'"\1":'  # wrap keys with double quotes
QUOTE_VALUES_REGEX = r":\s?(?!(\d+|true|false))(\w+)", r':"\2"'  # wrap values with double quotes, skip int or bool


def scape_urls(js_text: str) -> str:
    return js_text.replace("https:", HTTPS_PLACEHOLDER).replace("http:", HTTP_PLACEHOLDER)


def recover_urls(js_text: str) -> str:
    return js_text.replace(HTTPS_PLACEHOLDER, "https:").replace(HTTP_PLACEHOLDER, "http:")


def parse_js_vars(js_text: str) -> dict:
    data = {}
    lines = js_text.split(";")
    for line in lines:
        line = line.strip()
        if not (line and line.startswith("var ")):
            continue
        name_and_value = line.removeprefix("var ")
        name, value = name_and_value.split("=", 1)
        name = name.strip()
        value = value.strip()
        data[name] = value
        if value.startswith("{") or value.startswith("["):
            data[name] = parse_json_to_dict(value)
    return data


def parse_json_to_dict(js_text: str, use_regex: bool = True) -> dict:
    json_str = js_text.replace("\t", "").replace("\n", "").strip()
    json_str = replace_quotes(json_str)
    if use_regex:
        json_str = scape_urls(json_str)
        json_str = re.sub(*QUOTE_KEYS_REGEX, json_str)
        json_str = re.sub(*QUOTE_VALUES_REGEX, json_str)
        json_str = recover_urls(json_str)
    return json.loads(json_str)


def replace_quotes(js_text: str) -> str:
    return js_text.replace(",'", ',"').replace("':", '":').replace(", '", ', "').replace("' :", '" :')


def is_valid_key(key: str) -> bool:
    return not any(p in key for p in ("@", "m3u8"))


def clean_dict(data: dict, *keys_to_clean) -> None:
    """Modifies dict in place"""

    for key in keys_to_clean:
        inner_dict = data.get(key)
        if inner_dict and isinstance(inner_dict, dict):
            data[key] = {k: v for k, v in inner_dict.items() if is_valid_key(k)}

    for k, v in data.items():
        if isinstance(v, dict):
            continue
        data[k] = clean_value(v)


def clean_value(value: list | str | int) -> list | str | int | None:
    if isinstance(value, str):
        value = value.removesuffix("'").removeprefix("'")
        if value.isdigit():
            return int(value)
        return value

    if isinstance(value, list):
        return [clean_value(v) for v in value]
    return value


def get_javascript_variables(js_code: str) -> dict[str, Any]:
    """Parses JavaScript code with esprima and returns a dictionary of every variable declaration.

    Only extract literal values (no arimetic or strings operations)"""

    try:
        tree: nodes.Script = esprima.parseScript(js_code)  # type: ignore
        variables = {}
        for node in tree.body:
            if isinstance(node, nodes.VariableDeclaration):
                declarations: list[nodes.VariableDeclarator] = node.declarations
                for declaration in declarations:
                    var_name: str = declaration.id.name
                    variables[var_name] = None
                    if declaration.init:
                        variables[var_name] = extract_values_from_node(declaration.init)
        return variables

    except esprima.Error as e:
        msg = f"Esprima parse error: {e} \n {js_code = }"
        log_debug(msg)

    return {}


def extract_values_from_node(node: nodes.Node) -> Any:
    # variable
    if isinstance(node, nodes.Literal):
        return node.value

    # list
    if isinstance(node, nodes.ArrayExpression):
        arr = []
        for element in node.elements:
            value = extract_values_from_node(element)
            arr.append(value)
        return arr

    # dict
    if isinstance(node, nodes.ObjectExpression):
        obj = {}
        properties: list[nodes.Property] = node.properties
        for prop in properties:
            key_name: str = prop.key.value
            value = extract_values_from_node(prop.value)
            obj[key_name] = value

        return obj
