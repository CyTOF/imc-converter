import numpy as np
import pandas as pd
import xarray as xr
import tifffile
import xmltodict

from pathlib import Path
from typing import Union, List, Sequence, Generator
import mmap
import struct
import re


def _xyzc_to_arr(arr: np.ndarray, fill_missing: float=None) -> np.ndarray:
    """Transform long-form data with (x, y, z, *channels) columns to an image array
    indexed by (row, col, channel).
    Args:
        arr: array of data with columns (x, y, z, *channels)
        fill_missing: value to use to fill in missing data (assumes rectangular
            image, if max_x * max_y is not equal to number of rows).
    Returns:
        Multichannel image array indexed by (row, col, channel).
    """
    xsz, ysz = arr[:,:2].max(axis=0).astype(int) + 1
    csz = arr.shape[1] - 3
    if xsz * ysz != arr.shape[0] and fill_missing is not None:
        # Create new array with expected shape
        arr = np.vstack([arr, np.ones((xsz*ysz - arr.shape[0], 3+csz))*fill_missing])
    return arr[:,3:].reshape((ysz, xsz, csz))


def _is_missing_values(arr: np.ndarray) -> bool:
    """Return boolean if long-form X/Y/Z/Channel data is missing any values.
    Args:
        arr: array of data with columns (x, y, z, *channels).
    Returns:
        True if data contains NaN value in row or if number of rows is inconsistent
            with max_x * max_y, otherwise False.
    """
    nexpected = (arr[:,0].max() + 1) * (arr[:,1].max() + 1)
    return (arr.shape[0] != nexpected or np.isnan(arr).sum() > 0)


def read_mcd(path: Union[Path, str], fill_missing: float=None, encoding: str="utf-16-le"
            ) -> Generator[xr.DataArray, None, None]:
    """Read a Fluidigm IMC .mcd file and yields xarray DataArray
    (since MCD files can contain more than one image). 
    Args:
        path: path to IMC .mcd file.
        fill_missing: value to use to fill in missing image data. If not specified,
            an error will be raised if there is missing image data.
        encoding: specifies the Unicode encoding of the XML section (defaults to UTF-16-LE).
    Returns:
        A generator of xarray DataArrays containing multichannel image data.
    """
    with open(path, mode="rb") as fh:
        mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
        # MCD format documentation recommends searching from end for "<MCDPublic"
        offset = mm.rfind("<MCDPublic".encode(encoding))
        if offset == -1:
            raise ValueError(f"'{str(path)}' does not contain MCDPublic XML footer (try different encoding?).")
        mm.seek(offset)
        xml = mm.read().decode(encoding)
        # Parse xml, force Acquisition(Channel) to be list even if single item so we can iterate
        # (Can have multiple acquisitions/images in the same file...)
        root = xmltodict.parse(xml, 
            force_list=("Acquisition", "AcquisitionChannel"))["MCDPublic"]
        acquisitions = root["Acquisition"]
        for acq in acquisitions:
            # Get acquisition ID
            id_ = acq["ID"]
            # Get list of channels for this acquisition
            channels = [ch for ch in root["AcquisitionChannel"] 
                if ch["AcquisitionID"] == id_ and ch["ChannelName"] not in ("X", "Y", "Z")]
            channels = sorted(channels, key=lambda c: int(c["OrderNumber"]))
            # Parse 4-byte float32 values
            # Data consists of point values ordered X, Y, Z, C1, C2, ..., CN (and so on)
            if acq["SegmentDataFormat"] != "Float" or acq["ValueBytes"] != "4":
                raise NotImplementedError("Expected float32 data in 'SegmentDataFormat' tag.")
            mm.seek(int(acq["DataStartOffset"]))
            raw = mm.read(int(acq["DataEndOffset"]) - int(acq["DataStartOffset"]))
            arr = np.array(
                [struct.unpack("f", raw[i:i+4])[0] for i in range(0, len(raw), 4)],
                dtype=np.float32
            ).reshape((-1, len(channels) + 3))
            if fill_missing is None and _is_missing_values(arr):
                raise ValueError("Image data is missing values. Try specifying 'fill_missing'.")
            # Reshape long-form data to image
            img = _xyzc_to_arr(arr, fill_missing)
            channel_names = [
                f"{c['ChannelName']}_{c['ChannelLabel']}" 
                if c["ChannelLabel"] is not None else c["ChannelName"] for c in channels
            ]
            yield xr.DataArray(img, name=f"{Path(path).stem}_{id_}",
                dims=("y", "x", "c"), 
                coords={"x": range(img.shape[1]), "y": range(img.shape[0]),
                        "c": channel_names},
                attrs=acq,
            )
        mm.close()


def write_ometiff(imarr: xr.DataArray, outpath: Union[Path, str], summary: bool=False, **kwargs) -> None:
    """Write DataArray to a multi-page OME-TIFF file.
    Args:
        imarr: image DataArray object
        outpath: file to output to
        summary: whether to output MCDViewer summary file with export
        **kwargs: Additional arguments to tifffile.imwrite
    """
    outpath = Path(outpath)
    imarr = imarr.transpose("c", "y", "x")
    Nc, Ny, Nx = imarr.shape
    # Generate standard OME-XML
    channels_xml = '\n'.join(
        [f"""<Channel ID="Channel:0:{i}" Name="{channel}" SamplesPerPixel="1" />"""
            for i, channel in enumerate(imarr.c.values)]
    )
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
    <OME xmlns="http://www.openmicroscopy.org/Schemas/OME/2016-06"
            xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
            xsi:schemaLocation="http://www.openmicroscopy.org/Schemas/OME/2016-06 http://www.openmicroscopy.org/Schemas/OME/2016-06/ome.xsd">
        <Image ID="Image:0" Name="{outpath.stem}">
            <Pixels BigEndian="false"
                    DimensionOrder="XYZCT"
                    ID="Pixels:0"
                    Interleaved="false"
                    SizeC="{Nc}"
                    SizeT="1"
                    SizeX="{Nx}"
                    SizeY="{Ny}"
                    SizeZ="1"
                    PhysicalSizeX="1.0"
                    PhysicalSizeY="1.0"
                    Type="float">
                <TiffData />
                {channels_xml}
            </Pixels>
        </Image>
    </OME>
    """
    outpath.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(outpath, data=imarr.values, description=xml, contiguous=True, **kwargs)
    if summary:
        # Write MCDViewer summary, might be needed for compatibility with Visiopharm (?)
        summaryfname = outpath.name.rstrip(''.join(outpath.suffixes)) + "_summary.txt"
        rows = []
        for page, imchannel in enumerate(imarr):
            channel, label = str(imchannel.c.values).split("_")
            rows.append([page, channel, label, imchannel.values.min(), imchannel.values.max()])
        pd.DataFrame(rows, columns=["Page", "Channel", "Label", "MinValue", "MaxValue"]) \
          .to_csv(outpath.with_name(summaryfname), index=False, sep="\t")


def write_individual_tiffs(imarr: xr.DataArray, outdir: Union[Path, str], **kwargs) -> None:
    """Write DataArray to individual TIFF files in a folder.
    Args:
        imarr: image DataArray object
        outdir: folder to output to
        **kwargs: Additional arguments to tifffile.imwrite
    """
    imarr = imarr.transpose("c", "y", "x")
    outdir.mkdir(parents=True, exist_ok=True)
    for imchannel in imarr:
        tifffile.imwrite(Path(outdir) / f"{str(imchannel.c.values)}.tiff", data=imchannel.values, **kwargs)
