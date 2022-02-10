#!/usr/bin/env python
# coding: utf-8

# Copyright (c) Mito.
# Distributed under the terms of the Modified BSD License.

"""
Contains tests for the convert_args_to_series_type decorator.
"""
from mitosheet.sheet_functions.types.utils import BOOLEAN_SERIES, DATETIME_SERIES, NUMBER_SERIES, STRING_SERIES
import pandas as pd 
import pytest

from mitosheet.sheet_functions.types.decorators import convert_args_to_series_type


CONVERT_ARGS_TESTS = [
    (
        [pd.Series([True, True, True]), pd.Series([pd.to_datetime('12-12-2020')] * 3), pd.Series([0, 1, 2]), pd.Series(['True', 'True', 'True'])],
        BOOLEAN_SERIES,
        [pd.Series([True, True, True]), pd.Series([True, True, True]), pd.Series([False, True, True]), pd.Series([True, True, True])]
    ),
    (
        [pd.Series([True, True, True]), pd.Series([pd.to_datetime('12-12-2020')] * 3), pd.Series([0, 1, 2]), pd.Series(['12-12-2020', '12-12-2020', '12-12-2020'])],
        DATETIME_SERIES,
        [pd.Series([pd.to_datetime('12-12-2020')] * 3), pd.Series([pd.to_datetime('12-12-2020')] * 3)]
    ),
    (
        [pd.Series([True, True, True]), pd.Series([pd.to_datetime('12-12-2020')] * 3), pd.Series([0, 1, 2]), pd.Series(['12.12', '13.13', '14.14'])],
        NUMBER_SERIES,
        [pd.Series([1.0, 1.0, 1.0]), pd.Series([0, 1, 2]), pd.Series([12.12, 13.13, 14.14])]
    ),
    (
        [pd.Series([True, True, True]), pd.Series([pd.to_datetime('12-12-2020')] * 3), pd.Series([0, 1, 2]), pd.Series(['12-12-2020', '12-12-2020', '12-12-2020'])],
        STRING_SERIES,
        [pd.Series(['True', 'True', 'True']), pd.Series(['2020-12-12 00:00:00'] * 3), pd.Series(['0', '1', '2']), pd.Series(['12-12-2020'] * 3)]
    ),
]

@pytest.mark.parametrize("args, cast_output_type, result", CONVERT_ARGS_TESTS)
def test_filter_nan(args, cast_output_type, result):

    @convert_args_to_series_type(
        cast_output_type,
        on_uncastable_arg='skip',
        on_uncastable_arg_element='error'
    )
    def input_convert(*func_args):
        for arg1, arg2 in zip(func_args, result):
            assert arg1.equals(arg2)

    input_convert(*args)