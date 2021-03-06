"""
Validation methods for the NAPALM base.

See: https://napalm.readthedocs.io/en/latest/validate.html
"""
from __future__ import unicode_literals

import yaml

from napalm.base.exceptions import ValidationException
from napalm.base.utils import py23_compat

import copy
import re


# We put it here to compile it only once
numeric_compare_regex = re.compile(r"^(<|>|<=|>=|==|!=)(\d+(\.\d+){0,1})$")


def _get_validation_file(validation_file):
    try:
        with open(validation_file, 'r') as stream:
            try:
                validation_source = yaml.load(stream)
            except yaml.YAMLError as exc:
                raise ValidationException(exc)
    except IOError:
        raise ValidationException("File {0} not found.".format(validation_file))
    return validation_source


def _mode(mode_string):
    mode = {'strict': False}

    for m in mode_string.split():
        if m not in mode.keys():
            raise ValidationException("mode '{}' not recognized".format(m))
        mode[m] = True
    return mode


def _compare_getter_list(src, dst, mode):
    result = {"complies": True, "present": [], "missing": [], "extra": []}
    for src_element in src:
        found = False

        i = 0
        while True:
            try:
                intermediate_match = compare(src_element, dst[i])
                if isinstance(intermediate_match, dict) and intermediate_match["complies"] or \
                   not isinstance(intermediate_match, dict) and intermediate_match:
                    found = True
                    result["present"].append(src_element)
                    dst.pop(i)
                    break
                else:
                    i += 1
            except IndexError:
                break

        if not found:
            result["complies"] = False
            result["missing"].append(src_element)

    if mode["strict"] and dst:
        result["extra"] = dst
        result["complies"] = False

    return result


def _compare_getter_dict(src, dst, mode):
    result = {"complies": True, "present": {}, "missing": [], "extra": []}
    dst = copy.deepcopy(dst)  # Otherwise we are going to modify a "live" object

    for key, src_element in src.items():
        try:
            dst_element = dst.pop(key)
            result["present"][key] = {}
            intermediate_result = compare(src_element, dst_element)

            if isinstance(intermediate_result, dict):
                nested = True

                complies = intermediate_result["complies"]

                if not complies:
                    result["present"][key]['diff'] = intermediate_result
            else:
                complies = intermediate_result
                nested = False
                if not complies:
                    result["present"][key]["expected_value"] = src_element
                    result["present"][key]["actual_value"] = dst_element

            if not complies:
                result["complies"] = False

            result["present"][key]["complies"] = complies
            result["present"][key]["nested"] = nested
        except KeyError:
            result["missing"].append(key)
            result["complies"] = False

    if mode["strict"] and dst:
        result["extra"] = list(dst.keys())
        result["complies"] = False

    return result


def compare(src, dst):
    if isinstance(src, py23_compat.string_types):
        src = py23_compat.text_type(src)

    if isinstance(src, dict):
        mode = _mode(src.pop('_mode', ''))
        if 'list' in src.keys():
            if not isinstance(dst, list):
                # This can happen with nested lists
                return False

            return _compare_getter_list(src['list'], dst, mode)
        return _compare_getter_dict(src, dst, mode)

    elif isinstance(src, py23_compat.string_types):
        if src.startswith('<') or src.startswith('>'):
            cmp_result = _compare_numeric(src, dst)
            return cmp_result
        else:
            m = re.search(src, py23_compat.text_type(dst))
            if m:
                return bool(m)
            else:
                return src == dst

    elif(type(src) == type(dst) == list):
        pairs = zip(src, dst)
        diff_lists = [[(k, x[k], y[k])
                      for k in x if not re.search(x[k], y[k])]
                      for x, y in pairs if x != y]
        return empty_tree(diff_lists)

    else:
        return src == dst


def _compare_numeric(src_num, dst_num):
    """Compare numerical values. You can use '<%d','>%d'."""
    dst_num = float(dst_num)

    match = numeric_compare_regex.match(src_num)
    if not match:
        error = "Failed numeric comparison. Collected: {}. Expected: {}".format(dst_num, src_num)
        raise ValueError(error)

    operand = {
        "<": "__lt__",
        ">": "__gt__",
        ">=": "__ge__",
        "<=": "__le__",
        "==": "__eq__",
        "!=": "__ne__",
    }
    return getattr(dst_num, operand[match.group(1)])(float(match.group(2)))


def empty_tree(input_list):
    """Recursively iterate through values in nested lists."""
    for item in input_list:
        if not isinstance(item, list) or not empty_tree(item):
            return False
    return True


def compliance_report(cls, validation_file=None, validation_source=None):
    report = {}
    if validation_file:
        validation_source = _get_validation_file(validation_file)

    # collecting all data once per getter
    getters = {}
    for validation_check in validation_source:
        for getter, expected_results in validation_check.items():
            if getter != "get_config":
                key = expected_results.pop("_name", "") or getter
                kwargs = expected_results.pop('_kwargs', {})
                if not getters.get(getter):
                    getters[getter] = {}
                if getter not in ["ping", "is_alive", "traceroute", "get_route_to"]:
                    if not getters[getter].get('actual_results'):
                        getters[getter]["actual_results"] = getattr(cls, getter)(**kwargs)
                else:
                    getters[getter][key] = {}
                    getters[getter][key]["actual_results"] = getattr(cls, getter)(**kwargs)
    
    # creating reports per compliance check from validation file
    for validation_check in validation_source:
        for getter, expected_results in validation_check.items():
            if getter == "get_config":
                # TBD
                pass
            else:
                key = expected_results.pop("_name", "") or getter

                try:
                    kwargs = expected_results.pop('_kwargs', {})
                    if getter not in ["ping", "is_alive", "traceroute", "get_route_to"]:
                        actual_results = getters[getter]["actual_results"]
                    else:
                        actual_results = getters[getter][key]["actual_results"]
                    report[key] = compare(expected_results, actual_results)
                except NotImplementedError:
                    report[key] = {"skipped": True, "reason": "NotImplemented"}

    complies = all([e.get("complies", True) for e in report.values()])
    report["skipped"] = [k for k, v in report.items() if v.get("skipped", False)]
    report["complies"] = complies
    return report
