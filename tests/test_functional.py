"""Functional tests for reading and writing files."""

import os
import pickle
from pathlib import Path

import pytest
import xarray.testing
from imcconv import read_txt, read_mcd, ROIData


def unpickle_ref(path):
    with open(path.with_suffix(".pickle"), "rb") as f:
        refarr = pickle.load(f)
    return refarr


def test_read_txt_valid(txt_valid_path):
    arr = read_txt(txt_valid_path)
    refarr = unpickle_ref(txt_valid_path)
    assert arr.equals(refarr)


def test_read_txt_raises_error_on_missing_values(
    txt_missing_intensity_values_path, txt_missing_xy_rows_path
):
    with pytest.raises(ValueError):
        read_txt(txt_missing_intensity_values_path, fill_missing=None)
    with pytest.raises(ValueError):
        read_txt(txt_missing_xy_rows_path, fill_missing=None)


def test_read_txt_fill_missing_intensity_values(txt_missing_intensity_values_path):
    arr = read_txt(txt_missing_intensity_values_path, fill_missing=-1)
    refarr = unpickle_ref(txt_missing_intensity_values_path)
    assert arr.equals(refarr)


def test_read_txt_fill_missing_xy_rows(txt_missing_xy_rows_path):
    arr = read_txt(txt_missing_xy_rows_path, fill_missing=-1)
    refarr = unpickle_ref(txt_missing_xy_rows_path)
    assert arr.equals(refarr)


def test_read_txt_on_shuffled_rows(txt_valid_path):
    roi = ROIData.from_txt(txt_valid_path)
    arr = roi.as_dataarray(None)
    roi.df = roi.df.sample(frac=1)
    arr_shuffled = roi.as_dataarray(None)
    assert arr.equals(arr_shuffled)


def test_read_mcd_equivalent_to_read_txt(mcd_valid, mcd_valid_as_txt):
    mcd_roi = next(read_mcd(mcd_valid))
    txt_roi = read_txt(mcd_valid_as_txt)
    # There can be a slight precision difference so test using allclose
    xarray.testing.assert_allclose(mcd_roi, txt_roi)