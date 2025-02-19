#!/usr/bin/env python
# coding: utf-8

# Copyright (c) Saga Inc.
# Distributed under the terms of the GPL License.

"""
This file contains utilities for the rest of the mitosheet package
to interact with telemetry. See the README.md file in this folder for
more details.
"""

import platform
import sys
import time
from copy import copy
from typing import Any, Dict, Optional

import requests

from mitosheet.errors import MitoError, get_recent_traceback_as_list
from mitosheet.telemetry.anonymization_utils import anonymize_object, get_final_private_params_for_single_kv
from mitosheet.telemetry.private_params_map import LOG_EXECUTION_DATA_PUBLIC
from mitosheet.types import StepsManagerType
from mitosheet.user.location import get_location, is_docker
from mitosheet.user.schemas import UJ_EXPERIMENT, UJ_FEEDBACKS, UJ_FEEDBACKS_V2, UJ_INTENDED_BEHAVIOR, UJ_MITOSHEET_TELEMETRY, UJ_USER_EMAIL
from mitosheet.user.utils import is_local_deployment, is_pro

import analytics

# Write key taken from segement.com
analytics.write_key = '6I7ptc5wcIGC4WZ0N1t0NXvvAbjRGUgX' 


from mitosheet._version import __version__, package_name
from mitosheet.errors import MitoError, get_recent_traceback_as_list
from mitosheet.user import (UJ_STATIC_USER_ID, get_user_field,
                            is_local_deployment, is_running_test)

# If you want, you can optionally choose to print logs
PRINT_LOGS = False


try:
    import mitosheet_helper_private
    MITOSHEET_HELPER_PRIVATE = True
except ImportError:
    MITOSHEET_HELPER_PRIVATE = False


def telemetry_turned_on() -> bool:
    """
    Helper function that tells you if logging is turned on or
    turned off on the entire Mito instance
    """

    # If private helper is installed, then we don't log anything
    if MITOSHEET_HELPER_PRIVATE:
        return False

    # If the current package is mitosheet-private, then we don't log anything,
     # ever, under any circumstances - this is a custom distribution for a client
    if package_name == 'mitosheet-private':
        return False

    # If Mito Pro is on, then don't log anything
    if is_pro():
        return False

    telemetry = get_user_field(UJ_MITOSHEET_TELEMETRY) 
    return telemetry if telemetry is not None else False


def _get_anonymized_log_params(params: Dict[str, Any], steps_manager: Optional[StepsManagerType]=None) -> Dict[str, Any]:
    """
    Private params are where we _make sure_ that no private
    user data leaves the user's machine. We replace any potentially
    non-private params with private versions of them here.
    """
    private_params: Dict[str, Any] = dict()

    for key, value in params.items():
        private_params = {
            **private_params, 
            **get_final_private_params_for_single_kv(key, value, params, steps_manager)
        }

    # Prefix all the params with params_ so we can easily find them
    private_params = {'params_' + key: value for key, value in private_params.items()}
            
    return private_params


def _get_execution_data_log_params(steps_manager: Optional[StepsManagerType]=None) -> Dict[str, Any]:
    """
    Get the execution params as well, again making sure
    to remove any private data.
    """
    execution_data_params = {}

    # First, try and get the execution data from the stpe
    if steps_manager and steps_manager.curr_step.execution_data:
        for key, value in steps_manager.curr_step.execution_data.items():
            # Only take those items that are marked as public
            if key in LOG_EXECUTION_DATA_PUBLIC:
                execution_data_params['execution_data_' + key] = value
            # And make the rest private
            else:
                execution_data_params['execution_data_' + key] = anonymize_object(value)

    return execution_data_params

def _get_wsc_log_params(steps_manager: Optional[StepsManagerType]=None) -> Dict[str, Any]:
    """
    Get data from the widget state container that is useful for any
    log event. Note that none of this is private data.
    """

    if steps_manager:
        # We also get some metadata about the widget state container at this state
        # NOTE: we keep this as underscored with wsc for backwards compatibility with
        # our logs!
        return {
            'wsc_analysis_name': steps_manager.analysis_name,
            # NOTE: Change this when code fixing this logic is merged in
            'wsc_data_type_in_mito': str(steps_manager.data_type_in_mito),
            'wsc_local': is_local_deployment(),
            'wsc_curr_step_idx': steps_manager.curr_step_idx,
            'wsc_curr_step_type': steps_manager.curr_step.step_type,
            'wsc_public_interface_version': steps_manager.public_interface_version,
        }
    else:
        return {}

def _get_error_log_params(failed: bool=False, mito_error: Optional[MitoError]=None)-> Dict[str, Any]:
    """
    Get relevant logging data from any recently thrown error
    """

    # We also check there is an edit_error, and if there is, then we add the error logs
    if mito_error is not None or failed:
        recent_traceback = get_recent_traceback_as_list() 
        # TODO: if this is to long, we should chop it, and also take only the first 10k characters of the last line...
        return {
            'error_traceback': recent_traceback,
            # We get the last line of the error as it makes it much easier
            # to easily analyze on error messages 
            'error_traceback_last_line': recent_traceback[-1],
        }
    else:
        return {}

def _get_processing_time_log_params(steps_manager: Optional[StepsManagerType]=None, start_time: Optional[float]=None)-> Dict[str, Any]:
    """
    Get data relevant for measuring performance impact
    """

    processing_time_params = {}
     # We also log some timing information - which we round to a single decimal place just
    # so that we can bucket these items easily. Note we include a variety of roundings of 
    # the time, so that we can make sure to aggregate in Mixpanel well (which will die if 
    # it is given to many values).
    if start_time is not None:
        processing_time = time.perf_counter() - start_time
        processing_time_params['processing_time'] = round(processing_time, 1)
        processing_time_params['processing_time_seconds'] = int(round(processing_time, 0))
        processing_time_params['processing_time_seconds_ten'] = int(round(processing_time, -1))
        processing_time_params['processing_time_seconds_hundred'] = int(round(processing_time, -2))

        # If we just did an update, and this update has a pandas processing time, then we can calculate the
        # time that we spent as mito overhead vs. just executing pandas code
        if steps_manager and steps_manager.curr_step.execution_data and 'pandas_processing_time' in steps_manager.curr_step.execution_data:
            pandas_processing_time = steps_manager.curr_step.execution_data['pandas_processing_time']
            processing_time_params['processing_time_pandas'] = round(pandas_processing_time, 1)
            processing_time_params['processing_time_pandas_seconds'] = int(round(pandas_processing_time, 0))
            processing_time_params['processing_time_pandas_seconds_ten'] = int(round(pandas_processing_time, -1))
            processing_time_params['processing_time_pandas_seconds_hundred'] = int(round(pandas_processing_time, -2))

            # And we explicitly calculate the overhead
            overhead_processing_time = round(processing_time - pandas_processing_time, 1)
            processing_time_params['processing_time_overhead'] = overhead_processing_time
            processing_time_params['processing_time_overhead_seconds'] = int(round(overhead_processing_time, 0))
            processing_time_params['processing_time_overhead_seconds_ten'] = int(round(overhead_processing_time, -1))
            processing_time_params['processing_time_overhead_seconds_hundred'] = int(round(overhead_processing_time, -2))
        
    return processing_time_params


try:
    from jupyterlab import __version__ as jupyterlab_version
except:
    jupyterlab_version = 'No JupyterLab'
try:
    from notebook import __version__ as notebook_version
except:
    notebook_version = 'No notebook'
try:
    from pandas import __version__ as pandas_version
except:
    pandas_version = 'no pandas'
try:
    from ipywidgets import __version__ as ipywidgets_version
except:
    ipywidgets_version = 'no pandas'


location = None

def _get_environment_params() -> Dict[str, Any]:
    """
    Get data relevant for tracking the environment, so we can 
    ensure Mitosheet compatibility with any system
    """
    global location
    if location is None:
        location = get_location()
    
    # Add the python properties to every log event we can
    environment_params = {
        'version_python': sys.version_info,
        'version_pandas': pandas_version,
        'version_ipywidgets': ipywidgets_version,
        'version_jupyterlab': jupyterlab_version,
        'version_notebook': notebook_version,
        'version_mito': __version__,
        'package_name': package_name,
        'location': location,
        'is_docker': is_docker()
    }

    return environment_params

experiment = None
def _get_experiment_params() -> Dict[str, Any]:
    """
    Get data relevant for tracking the experiment, so we can 
    see how the experiment is running

    NOTE: This must match the function in the installer
    """
    global experiment
    if experiment is None:
        from mitosheet.experiments.experiment_utils import get_current_experiment
        experiment = get_current_experiment()

    if experiment is None:
        experiment_params = {'experiment_id': 'No experiment'}
    else:
        experiment_params = {
            'experiment_id': experiment["experiment_id"],
            'experiment_variant': experiment["variant"],
            f'experiment_{experiment["experiment_id"]}': experiment['variant']
        }

    return experiment_params

def log_event_processed(event: Dict[str, Any], steps_manager: StepsManagerType, failed: bool=False, mito_error: Optional[MitoError]=None, start_time: Optional[float]=None) -> None:
    """
    Helper function for logging when an event is processed by the backend,
    including an edit event, an api call, or an update event. 

    It generates mulitple logs so that aggregating and breaking down this 
    data is easier.

    NOTE: if processing the event fails, then failed should be True. If there was an
    edit error that was thrown during the processing of the event, then edit_error
    should be set to that error.
    """

    # We choose to log the event type, as it is the best high-level item for our logs
    # and we append a _failed if the event failed in doing this.
    log_event: str = event['type'] + ('_failed' if failed else '')
    params_for_final_log = {
        # NOTE: We make a copy here so we don't modify the actual params
        # dict, which we don't want to do as it's used elsewhere!
        'params': copy(event['params']),
        'steps_manager': steps_manager,
        'failed': failed,
        'mito_error': mito_error,
        'start_time': start_time
    }

    log(
        log_event, 
        **params_for_final_log
    )

    if failed:
        # We also generate a double log in the case of errors, whenever anything fails. This allows
        # us to easily track the number of users who are getting errors
        log(
            'error', 
            **params_for_final_log
        )
    else:
        # We also generate a single aggregate log for each of the different
        # types of events the user sends to the backend. As with the above, 
        # this allows us to easily aggregate across different types of events
        # and track general trends (at the cost of making the logs look a bit
        # messier to human eyes)
        params_for_final_log['params']['log_event'] = log_event
        log(
            event['event'], 
            **params_for_final_log
        )



def identify() -> None:
    """
    Helper function for identifying a user. We just take
    their python version, mito version, and email.
    """
    if not telemetry_turned_on():
        return

    static_user_id = get_user_field(UJ_STATIC_USER_ID)
    user_email = get_user_field(UJ_USER_EMAIL)
    intended_behavior = get_user_field(UJ_INTENDED_BEHAVIOR)
    feedbacks = get_user_field(UJ_FEEDBACKS)
    feedbacks_v2 = get_user_field(UJ_FEEDBACKS_V2)
    local = is_local_deployment()
    operating_system = platform.system()

    
    if not is_running_test():
        # NOTE: we do not log anything when tests are running
        analytics.identify(static_user_id, {
            'version_python': sys.version_info,
            'version_pandas': pandas_version,
            'version_ipywidgets': ipywidgets_version,
            'version_sys': sys.version,
            'version_mito': __version__,
            'package_name': package_name, 
            'version_jupyterlab': jupyterlab_version,
            'version_notebook': notebook_version,
            'operating_system': operating_system,
            'email': user_email,
            'local': local,
            UJ_INTENDED_BEHAVIOR: intended_behavior,
            UJ_FEEDBACKS: feedbacks,
            UJ_FEEDBACKS_V2: feedbacks_v2
        })


def log(log_event: str, params: Optional[Dict[str, Any]]=None, steps_manager: Optional[StepsManagerType]=None, failed: bool=False, mito_error: Optional[MitoError]=None, start_time: Optional[float]=None) -> None:
    """
    This function is the entry point for all logging. It collects
    all relevant parameters, exeuction data, and more info while
    making sure to anonymize all data. 

    Then, if telemetry is not turned off and we are not running tests,
    we log this information.
    """
    if params is None:
        params = {}


    final_params: Dict[str, Any] = {}

    # First, get the private params
    final_params = {**final_params, **_get_anonymized_log_params(params, steps_manager=steps_manager)}

    # Then, get the execution data from the steps
    final_params = {**final_params, **_get_execution_data_log_params(steps_manager=steps_manager)}
    
    # Then, get the logs for the specific analysis we're in
    final_params = {**final_params, **_get_wsc_log_params(steps_manager=steps_manager)}

    # Then, get the logs for the error (if there is one)
    final_params = {**final_params, **_get_error_log_params(failed=failed, mito_error=mito_error)}

    # Then, get the logs for the processing time of the operation
    final_params = {**final_params, **_get_processing_time_log_params(steps_manager=steps_manager, start_time=start_time)}

    # Then, get the params for the environment 
    final_params = {**final_params, **_get_environment_params()}

    # Then, get the params for the all experiments
    final_params = {**final_params, **_get_experiment_params()}

    # Finially, do the acutal logging. We do not log anything when tests are
    # running, or if telemetry is turned off
    if not is_running_test() and telemetry_turned_on():
        analytics.track(
            get_user_field(UJ_STATIC_USER_ID), 
            log_event, 
            final_params
        )

    # If we want to print the logs for debugging reasons, then we print them as well
    if PRINT_LOGS:
        print(
            log_event, 
            final_params
        )

    analytics_url = steps_manager.mito_config.get_analytics_url() if steps_manager is not None else None
    if analytics_url is not None:
        requests.post(
            analytics_url,
            json={
                'user_id': get_user_field(UJ_STATIC_USER_ID),
                'log_event': log_event
            }
        )