# -*- coding: utf-8 -*-

"""Pcaxis Parser module

This module obtains a pandas DataFrame of tabular data from a PC-Axis file or URL.
Reads data and metadata from PC-Axis into a dataframe and dictionary, and returns a
dictionary containing both structures.

Example:
    from etlstat.extractor.pcaxis import *

    dict = from_pc_axis(self.base_path + 'px/2184.px', encoding='ISO-8859-2')

References:
    PX-file format specification AXIS-VERSION 2013:
        https://www.scb.se/Upload/PC-Axis/Support/Documents/PX-file_format_specification_2013.pdf

Todo:
    meta_split: "NOTE" attribute can be multiple, but only the last one is added to the dictionary
"""

import itertools


import re
import logging
import requests
import numpy
import pandas

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def uri_type(uri):
    """
    Determines the type of URI.

    Args:
        uri (str): pc-axis file name or URL

    Returns:
        str_type (str): 'URL' | 'FILE'

    ..  Regex debugging:
        https://pythex.org/
    """
    str_type = 'FILE'

    # django url validation regex:
    regex = re.compile(r'^(?:http|ftp)s?://' # http:// or https://
                       #domain...
                       r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+'
                       r'(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|'
                       r'localhost|' #localhost...
                       r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})' # ...or ip
                       r'(?::\d+)?' # optional port
                       r'(?:/?|[/?]\S+)$', re.IGNORECASE)
    if re.match(regex, uri):
        str_type = 'URL'

    return str_type



def read(uri, encoding, timeout=10):
    """
    Reads a text file from file system or URL.

    Args:
        uri (str): file name or URL
        encoding (str): charset encoding
        timeout (int): request timeout; optional

    Returns:
        pc_axis (str): file contents.
    """

    raw_pcaxis = ''

    if uri_type(uri) == 'URL':
        try:
            response = requests.get(uri, stream=True, timeout=timeout)
            response.raise_for_status()
            response.encoding = encoding
            raw_pcaxis = response.text
            response.close()
        except requests.exceptions.ConnectTimeout as connect_timeout:
            logger.error('ConnectionTimeout = %s', str(connect_timeout))
            raise
        except requests.exceptions.ConnectionError as connection_error:
            logger.error('ConnectionError = %s', str(connection_error))
            raise
        except requests.exceptions.HTTPError as http_error:
            logger.error('HTTPError = %s', str(http_error.response.status_code) + ' ' +
                         http_error.response.reason)
            raise
        except requests.exceptions.InvalidURL as url_error:
            logger.error('URLError = ' + url_error.response.status_code + ' ' +
                         url_error.response.reason)
            raise
        except Exception:
            import traceback
            logger.error('Generic exception: %s', traceback.format_exc())
            raise
    else: # file parsing
        file_object = open(uri, encoding=encoding)
        raw_pcaxis = file_object.read()
        file_object.close()

    return raw_pcaxis


def metadata_extract(pc_axis):
    """
    Extracts metadata and data from pc-axis file contents.

    Args:
        pc_axis (str): pc_axis file contents.

    Returns:
        meta (list of string): each item conforms to pattern ATTRIBUTE=VALUES
        data (string): data values
    """
    # replace new line characters with blank
    pc_axis = pc_axis.replace('\n', ' ').replace('\r', ' ')

    # split file into metadata and data sections
    metadata, data = pc_axis.split('DATA=')
    # meta: list of strings that conforms to pattern ATTRIBUTE=VALUES
    metadata_attributes = re.findall('([^=]+=[^=]+)(?:;|$)', metadata)
    # remove trailing blanks and final semicolon
    data = data.strip().rstrip(';')
    for i, item in enumerate(metadata_attributes):
        metadata_attributes[i] = item.strip().rstrip(';')

    return metadata_attributes, data


def metadata_split_to_dict(metadata_elements):
    """
    Splits the list of metadata elements into a dictionary of multi-valued keys.

    Args:
        metadata_elements (list of string): pairs ATTRIBUTE=VALUES

    Returns:
        metadata (dictionary): {'attribute1': ['value1', 'value2', ... ], ...}
    """
    metadata = {}

    for element in metadata_elements:
        name, values = element.split('=')
        # remove double quotes from key
        name = name.replace('"', '')
        # split values delimited by double quotes into list
        # additionally strip leading and trailing blanks
        metadata[name] = re.findall('"[ ]*(.+?)[ ]*"+?', values)

    return metadata


def get_dimensions(metadata):
    """
    Reads STUB and HEADING values from metadata dictionary.

    Args:
        metadata: dictionary of metadata

    Returns:
        dimension_names (list)
        dimension_members (list)
    """

    dimension_names = []
    dimension_members = []

    # add STUB and HEADING elements to a list of dimension names
    # add VALUES of STUB and HEADING to a list of dimension members
    stubs = metadata['STUB']
    for stub in stubs:
        dimension_names.append(stub)
        stub_values = []
        raw_stub_values = metadata['VALUES(' + stub + ')']
        for value in raw_stub_values:
            stub_values.append(value)
        dimension_members.append(stub_values)

    # add HEADING values to the list of dimension members
    headings = metadata['HEADING']
    for heading in headings:
        dimension_names.append(heading)
        heading_values = []
        raw_heading_values = metadata['VALUES(' + heading + ')']
        for value in raw_heading_values:
            heading_values.append(value)
        dimension_members.append(heading_values)

    return dimension_names, dimension_members


def build_dataframe(dimension_names, dimension_members, data_values):
    """
    Builds a data frame by adding the cartesian product of dimension members,
    plus series of data.

    Args:
        dimension_names (list of string)
        dimension_members (list of string)
        data_list (list of string)

    Returns:
        data_frame (pandas data frame)
    """

    # cartesian product of dimension members
    dim_exploded = list(itertools.product(*dimension_members))

    data = pandas.DataFrame(data=dim_exploded, columns=dimension_names)

    # convert data values from string to float
    for index, value in enumerate(data_values):
        try:
            data_values[index] = float(value)
        except ValueError:
            data_values[index] = numpy.nan

    # column of data values
    data['DATA'] = pandas.Series(data_values)

    return data



def parse(uri, encoding, timeout=10):
    """
    Extracts metadata and data sections from pc-axis.

    Args:
        uri (str): file name or URL
        encoding (str): charset encoding
        timeout (int): request timeout in seconds; optional

    Returns:
         pc_axis_dict (dictionary): dictionary of metadata and pandas data frame
            METADATA: dictionary of metadata
            DATA: pandas data frame
    """

    # get file content or URL stream
    try:
        pc_axis = read(uri, encoding, timeout)
    except ValueError:
        import traceback
        logger.error('Generic exception: %s', traceback.format_exc())
        raise


    # metadata and data extraction and cleaning
    metadata_elements, raw_data = metadata_extract(pc_axis)

    # stores raw metadata into a dictionary
    metadata = metadata_split_to_dict(metadata_elements)

    # explode raw data into a list of float values
    data_values = raw_data.split()

    # extract dimension names and members from 'meta_dict' STUB and HEADING keys
    dimension_names, dimension_members = get_dimensions(metadata)

    # build a data frame
    data = build_dataframe(dimension_names, dimension_members, data_values)

    # dictionary of metadata and data (pandas data frame)
    parsed_pc_axis = {
        'METADATA': metadata,
        'DATA': data
    }
    return parsed_pc_axis
