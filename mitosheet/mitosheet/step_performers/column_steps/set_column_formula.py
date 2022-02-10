#!/usr/bin/env python
# coding: utf-8

# Copyright (c) Mito.
# Distributed under the terms of the Modified BSD License.

import pandas as pd

from copy import deepcopy
from mitosheet.step_performers.step_performer import StepPerformer
from typing import Any, Dict, List, Optional, Set, Tuple
from mitosheet.state import State
from mitosheet.sheet_functions.types.utils import get_mito_type
from mitosheet.topological_sort import creates_circularity, subgraph_from_starting_column_id, topological_sort_columns
from mitosheet.sheet_functions import FUNCTIONS
from mitosheet.parser import parse_formula

from mitosheet.errors import (
    MitoError,
    make_circular_reference_error,
    make_execution_error, 
    make_no_column_error, 
    make_operator_type_error, 
    make_unsupported_function_error, 
    make_wrong_column_metatype_error
)
from mitosheet.types import ColumnHeader, ColumnID

class SetColumnFormulaStepPerformer(StepPerformer):
    """
    A set_column_formula step, which allows you to set the formula
    of a given column in the sheet (and then recalculates this column)
    and it's dependents.
    """

    @classmethod
    def step_version(cls) -> int:
        return 2

    @classmethod
    def step_type(cls) -> str:
        return 'set_column_formula'

    @classmethod
    def step_display_name(cls) -> str:
        return 'Set Column Formula'
    
    @classmethod
    def step_event_type(cls) -> str:
        return 'set_column_formula_edit'

    @classmethod
    def saturate(cls, prev_state: State, params: Dict[str, Any]) -> Dict[str, Any]:
        sheet_index = params['sheet_index']
        column_id = params['column_id']
        params['old_formula'] = prev_state.column_spreadsheet_code[sheet_index][column_id]
        column_header = prev_state.column_ids.get_column_header_by_id(sheet_index, column_id)
        column_headers = prev_state.dfs[sheet_index].keys()
        
        # If the user submits an empty formula, we just set it equal to zero
        if params['new_formula'] == '':
            params['new_formula'] = '=0'
        else:
            try:
                # Try and parse the formula, letting it throw errors if it
                # is invalid
                parse_formula(params['new_formula'], column_header, column_headers, throw_errors=True)
            except:
                params['new_formula'] = _get_fixed_invalid_formula(params['new_formula'], column_header, column_headers)

        # By default, we don't do anything with the saturate
        return params

    @classmethod
    def execute( # type: ignore
        cls,
        prev_state: State,
        sheet_index: int,
        column_id: ColumnID,
        old_formula: str,
        new_formula: str,
        **params
    ) -> Tuple[State, Optional[Dict[str, Any]]]:
        column_header = prev_state.column_ids.get_column_header_by_id(sheet_index, column_id)

        # First, we check the column_metatype, and make sure it's a formula
        if prev_state.column_metatype[sheet_index][column_id] != 'formula':
            raise make_wrong_column_metatype_error(column_header, error_modal=False)

        # If nothings changed, there's no work to do
        if (old_formula == new_formula):
            return prev_state, None

        column_headers = prev_state.dfs[sheet_index].keys()

        # Then we try and parse the formula
        new_python_code, new_functions, new_dependencies_column_headers = parse_formula(
            new_formula, 
            column_header,
            column_headers
        )
        new_dependencies = set(prev_state.column_ids.get_column_ids(sheet_index, new_dependencies_column_headers))

        # We check that the formula doesn't reference any columns that don't exist
        missing_columns = new_dependencies.difference(prev_state.column_metatype[sheet_index].keys())
        if any(missing_columns):
            raise make_no_column_error(missing_columns, error_modal=False)

        # The formula can only reference known formulas
        missing_functions = new_functions.difference(set(FUNCTIONS.keys()))
        if any(missing_functions):
            raise make_unsupported_function_error(missing_functions, error_modal=False)

        # Then, we get the list of old column dependencies and new dependencies
        # so that we can update the graph
        _, _, old_dependencies_column_headers = parse_formula(old_formula, column_header, column_headers)
        old_dependencies = set(prev_state.column_ids.get_column_ids(sheet_index, old_dependencies_column_headers))

        # Before changing any variables, we make sure this edit didn't
        # introduct any circularity
        circularity = creates_circularity(
            prev_state.column_evaluation_graph[sheet_index], 
            column_id,
            old_dependencies,
            new_dependencies
        )
        if circularity:
            raise make_circular_reference_error(error_modal=False)

        # We check out a new step
        post_state = deepcopy(prev_state)

        # Update the column formula, and then execute the new formula graph
        try:
            _update_column_formula_in_step(post_state, sheet_index, column_id, old_formula, new_formula)
            refresh_dependant_columns(post_state, post_state.dfs[sheet_index], sheet_index)
        except MitoError as e:
            # Catch the error and make sure that we don't set the error modal
            e.error_modal = False
            raise e
        except:
            raise make_execution_error(error_modal=False)


        # Finially, update the type of the filters of this column, for all the filters
        # TODO: fix bug where we have to update downstream types, but note that
        # it would just be really nice if we didn't have to store the type here, and could
        # just get it dynamically...
        new_type = get_mito_type(post_state.dfs[sheet_index][column_header])
        post_state.column_type[sheet_index][column_id] = new_type
        post_state.column_filters[sheet_index][column_id]['filters'] = [
            {'type': new_type, 'condition': filter_['condition'], 'value': filter_['value']} 
            for filter_ in prev_state.column_filters[sheet_index][column_id]['filters']
        ]

        return post_state, None

    @classmethod
    def transpile( # type: ignore
        cls,
        prev_state: State,
        post_state: State,
        execution_data: Optional[Dict[str, Any]],
        sheet_index: int,
        column_id: ColumnID,
        old_formula: str,
        new_formula: str
    ) -> List[str]:
        """
        Transpiles an set_column_formula step to python code!
        """
        return transpile_dependant_columns(post_state, sheet_index, column_id)

    @classmethod
    def describe( # type: ignore
        cls,
        sheet_index: int,
        column_id: ColumnID,
        old_formula: str,
        new_formula: str,
        df_names=None,
        **params
    ) -> str:
        if df_names is not None:
            df_name = df_names[sheet_index]
            return f'Set {column_id} in {df_name} to {new_formula}'
        return f'Set {column_id} to {new_formula}'

    @classmethod
    def get_modified_dataframe_indexes( # type: ignore
        cls, 
        sheet_index: int,
        column_id: ColumnID,
        old_formula: str,
        new_formula: str,
        **params
    ) -> Set[int]:
        return {sheet_index}


def _update_column_formula_in_step(
        post_state: State,
        sheet_index: int,
        column_id: ColumnID,
        old_formula: str,
        new_formula: str,
        update_from_rename: bool=False
    ) -> None:
    """
    A  helper function for updating the formula of a column. It assumes
    that the passed information is all _correct_ and will not:
    1. Introduce a circular reference error.
    2. Add an invalid formula.

    It DOES NOT "reexecute" the dataframes, just updates the state variables.
    """
    column_header = post_state.column_ids.get_column_header_by_id(sheet_index, column_id)
    column_headers = post_state.dfs[sheet_index].keys()

    new_python_code, _, new_dependencies = parse_formula(
        new_formula, 
        column_header,
        column_headers
    )

    _, _, old_dependencies = parse_formula(
        old_formula, 
        column_header,
        column_headers
    )

    post_state.column_spreadsheet_code[sheet_index][column_id] = new_formula
    post_state.column_python_code[sheet_index][column_id] = new_python_code

    # Update the column dependency graph, if this is not just an update
    # from a rename (where the dependency graph does not change)
    if not update_from_rename:
        for old_dependency_column_header in old_dependencies:
            old_dependency_column_id = post_state.column_ids.get_column_id_by_header(sheet_index, old_dependency_column_header)
            post_state.column_evaluation_graph[sheet_index][old_dependency_column_id].remove(column_id)
        for new_dependency_column_header in new_dependencies:
            new_dependency_column_id = post_state.column_ids.get_column_id_by_header(sheet_index, new_dependency_column_header)
            post_state.column_evaluation_graph[sheet_index][new_dependency_column_id].add(column_id)


def _get_fixed_invalid_formula(
        new_formula: str, 
        column_header: ColumnHeader, 
        column_headers: List[ColumnHeader]
    ) -> str:
    """
    A helper function that, given a formula, will try and fix
    any common errors with it. 

    Currently, the common errors are just checking if the formula
    is missing some number of parenthases at the end.

    Returns the fixed formula. If it cannot fix the formula, then 
    returns the original formula (so that execution continues as 
    normal, to report the error to the user).
    """
    POTENTIAL_VALID_FORMULAS = [
        # Check the original formula, just in case it actually is valid
        new_formula,
        new_formula + ')',
        new_formula + '))',
        new_formula + ')))',
    ]
    for fixed_formula in POTENTIAL_VALID_FORMULAS:
        try:
            # Parse the formula, and return if it is valid
            parse_formula(fixed_formula, column_header, column_headers, throw_errors=True)
            return fixed_formula
        except:
            pass
    
    return new_formula


def get_details_from_operator_type_error(error: TypeError) -> Optional[Tuple[str, str, str]]:
    """
    We detect operator errors by checking the error string, which has the format:

    If you write 1 + 'true'
    - unsupported operand type(s) for /: 'int' and 'str' 

    If you write 1 + pd.Series(['true'])
    - can only concatenate str (not "int") to str

    If you write 10 ^ pd.Series(['true'])
    - Cannot perform 'rxor' with a dtyped [object] array and scalar of type [bool]
    If you write pd.Series(['true']) ^ 10
    - Cannot perform 'xor' with a dtyped [object] array and scalar of type [bool]

    On Python 3.6, there is one error when you try and append a number to 
    a string, where it says `must be str, not int`.

    ^ NOTE: the above two errors makes pretty much no sense at all, but it appears
    to be casting the number to a boolean? So we choose to handle this as a number,
    as this is when you would be 

    Returns None if the passed error is not an operator type error. Otherwise, returns
    a triple of (operator, first argument type, second argument type).
    """
    error_message = str(error)
    # Handle case where standard python primitive types are used incorrectly
    if error_message.startswith('unsupported operand type'):
        # Then, we get the specific operator by checking right before the :
        operator = error_message.split(':')[0].split(' ')[-1].strip()
        # And we get the specific types - see above format for how this parsing works!
        arg_one_type = error_message.split(':')[-1].split('\'')[1]
        arg_two_type = error_message.split(':')[-1].split('\'')[-2]
        return (operator, arg_one_type, arg_two_type)
    # Handle case where pandas dataframes are part of the operator, for a concatenate
    if error_message.startswith('can only concatenate str'):
        # We cannot tell the order here (e.g. we don't know if a string was concated
        # to an integer, or the other way around) b/c the error doesn't say so, so we just
        # do our best 
        other_type = error_message.split('"')[1]
        return ('+', 'str', other_type)
    # Handle a ^
    if error_message.startswith('Cannot perform \'rxor\' with a dtyped') or error_message.startswith('Cannot perform \'xor\' with a dtyped'):
        # This error message might be totally wrong, but there is some weird
        # casting that goes on in the error message that makes it hard to tell. As such,
        # we report the most likely error.
        return ('^', 'number', 'str')
    if error_message.startswith('must be str, not'):
        # When adding a number to a string
        return ('+', 'str', 'number')

    return None


def refresh_dependant_columns(post_state: State, df: pd.DataFrame, sheet_index: int) -> None:
    """
    Helper function for refreshing the columns that are dependant on the column we are changing. 
    """

    topological_sort = topological_sort_columns(post_state.column_evaluation_graph[sheet_index])

    for column_id in topological_sort:
        # Exec the code, where the df is the original dataframe
        # See explination here: https://www.tutorialspoint.com/exec-in-python
        try:
            exec(
                post_state.column_python_code[sheet_index][column_id],
                {'df': df}, 
                FUNCTIONS
            )
        except TypeError as e:
            # We catch TypeErrors specificially, so that we can case on operator errors, to 
            # give better error messages
            operator_type_error_details = get_details_from_operator_type_error(e)
            if operator_type_error_details is not None:
                # If there is an operator error, we handle it specially, to give the user
                # more information about how to recover
                raise make_operator_type_error(*operator_type_error_details)
            else:
                # If it's not an operator error, we just propagate the error up
                raise e
        except NameError as e:
            # If we have a column header that does not exist in the formula, we may
            # throw a name error, in which case we alert the user
            column_header = str(e).split('\'')[1]
            raise make_no_column_error({column_header})


def transpile_dependant_columns(
        post_state: State, 
        sheet_index: int, 
        column_id: ColumnID
    ) -> List[str]: 
    """
    Use this helper function when making a change to a column and you want to transpile
    the columns that are dependant on the column you changed. 
    """
    code = []

    # We only look at the sheet that was changed, and sort the columns, taking only
    # those downstream from the changed columns
    subgraph = subgraph_from_starting_column_id(post_state.column_evaluation_graph[sheet_index], column_id)
    topological_sort = topological_sort_columns(subgraph)

    # We compile all of their formulas
    for other_column_id in topological_sort:
        column_formula_changes = post_state.column_python_code[sheet_index][other_column_id]
        if column_formula_changes != '':
            # We replace the data frame in the code with it's parameter name!
            # NOTE: we check for df[ to increase the odds that we don't replace
            # something other than the dataframe name itself (e.g. replacing a column
            # name with the letters "df" inside of them
            column_formula_changes = column_formula_changes.strip().replace('df[', f'{post_state.df_names[sheet_index]}[')
            code.append(column_formula_changes)

    return code