#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# Copyright (C) 2015-2017 by Christian Tremblay, P.Eng <christian.tremblay@servisysDeviceObject.com>
# Licensed under LGPLv3, see file LICENSE in this source tree.
#
"""
Device.py - describe a BACnet Device

"""
import asyncio
import os.path

# --- standard Python modules ---
from collections import namedtuple
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

try:
    import pandas as pd

    _PANDAS = True
except ImportError:
    _PANDAS = False

try:
    from xlwings import Chart, Range, Sheet, Workbook  # noqa E401

    _XLWINGS = True
except ImportError:
    _XLWINGS = False


# --- this application's modules ---
from bacpypes3.basetypes import ServicesSupported

# from ...bokeh.BokehRenderer import BokehPlot
from ...db.sql import SQLMixin
from ...tasks.DoOnce import DoOnce
from ..io.IOExceptions import (
    BadDeviceDefinition,
    DeviceNotConnected,
    NoResponseFromController,
    RemovedPointException,
    SegmentationNotSupported,
    WritePropertyException,
    WrongParameter,
)
from ..utils.notes import note_and_log
from .mixins.read_mixin import ReadProperty, ReadPropertyMultiple
from .Points import BooleanPoint, EnumPoint, NumericPoint, OfflinePoint, Point
from .Virtuals import VirtualPoint

# ------------------------------------------------------------------------------


class DeviceProperties(object):
    def __init__(self):
        self.name: str = "Unknown"
        self.address: Optional[str] = None
        self.device_id: Optional[int] = None
        self.network: Optional[Any] = None
        self.pollDelay: Optional[int] = None
        self.objects_list: Optional[List] = None
        self.pss: ServicesSupported = ServicesSupported()
        self.multistates: Optional[Dict] = None
        self.db_name: Optional[str] = None
        self.segmentation_supported: bool = True
        self.history_size: Optional[int] = None
        self.save_resampling: str = "1s"
        self.clear_history_on_save: Optional[bool] = None
        self.bacnet_properties: Dict = {}
        self.auto_save: Optional[bool] = None
        self.fast_polling: bool = False
        self.vendor_id: int = 0
        self.ping_failures: int = 0

    @property
    def asdict(self) -> Dict:
        return self.__dict__

    def __repr__(self):
        return "{}".format(self.asdict)


@note_and_log
class Device(SQLMixin):
    """
    This class represents a BACnet device. It provides methods to read, write, simulate, and release
    communication with the device on the network.

    Parameters:
    address (str, optional): The address of the device (e.g., '2:5'). Defaults to None.
    device_id (int, optional): The BACnet device ID (boid). Defaults to None.
    network (BAC0.scripts.ReadWriteScript.ReadWriteScript, optional): Defined by BAC0.connect(). Defaults to None.
    poll (int, optional): If greater than 0, the device will poll every point each x seconds. Defaults to None.
    from_backup (str, optional): SQLite backup file. Defaults to None.
    segmentation_supported (bool, optional): When set to False, BAC0 will not use read property multiple to poll the device. Defaults to None.
    object_list (list, optional): User can provide a custom object list for the creation of the device. The object list must be built using the same pattern returned by bacpypes when polling the objectList property. Defaults to None.
    auto_save (bool or int, optional): If False or 0, auto_save is disabled. To activate, pass an integer representing the number of polls before auto_save is called. Will write the histories to SQLite db locally. Defaults to None.
    clear_history_on_save (bool, optional): If set to True, will clear device history. Defaults to None.

    """

    def __init__(
        self,
        address: Optional[str] = None,
        device_id: Optional[int] = None,
        network: Optional[Any] = None,
        *,
        poll: int = 10,
        from_backup: Optional[str] = None,  # filename of backup
        segmentation_supported: bool = True,
        object_list: Optional[List] = None,
        auto_save: bool = False,
        save_resampling: str = "1s",
        clear_history_on_save: bool = False,
        history_size: Optional[int] = None,
        reconnect_on_failure: bool = True
    ):
        self.properties = DeviceProperties()
        self.initialized = False
        self.properties.address = address
        self.properties.device_id = device_id
        self.properties.network = network
        self.properties.pollDelay = poll
        self.properties.fast_polling = True if poll < 10 else False
        self.properties.name = ""
        self.properties.vendor_id = 0
        self.properties.objects_list = []
        self.properties.pss = ServicesSupported()
        self.properties.multistates = {}
        self.properties.auto_save = auto_save
        self.properties.save_resampling = save_resampling
        self.properties.clear_history_on_save = clear_history_on_save
        self.properties.history_size = history_size
        self._reconnect_on_failure = reconnect_on_failure

        self.segmentation_supported = segmentation_supported
        self.custom_object_list = object_list

        # self.db = None
        # Todo : find a way to normalize the name of the db
        self.properties.db_name = None

        self.points = []
        self._list_of_trendlogs = {}

        self._polling_task = namedtuple("_polling_task", ["task", "running"])
        self._polling_task.task = None
        self._polling_task.running = False

        self._find_overrides_progress = 0.0
        self._find_overrides_running = False
        self._release_overrides_progress = 0.0
        self._release_overrides_running = False

        self.note("Controller initialized")

        if from_backup:
            filename = from_backup
            db_name = filename.split(".")[0]
            self.properties.network = None
            if os.path.isfile(filename):
                self.properties.db_name = db_name

            else:
                raise FileNotFoundError("Can't find {} on drive".format(filename))
        else:
            if (
                self.properties.network
                and self.properties.address
                and self.properties.device_id is not None
            ):
                pass
            else:
                raise BadDeviceDefinition(
                    "Please provide address, device id and network or specify from_backup argument"
                )

        self.initialized = True

    async def new_state(self, newstate: Any) -> None:
        """
        Changes the state of the device.

        This method forms the basis of the state machine mechanism and is used to transition between device states.
        It also calls the state initialization function.

        :param newstate: The new state to transition to.
        :type newstate: Any
        :return: None
        """
        self._log.info(
            "Changing device state to {}".format(str(newstate).split(".")[-1])
        )
        self.__class__ = newstate
        await self._init_state()

    async def _init_state(self) -> None:
        """
        Execute additional code upon state modification
        """
        raise NotImplementedError()

    def connect(self) -> None:
        """
        Connect the device to the network
        """
        raise NotImplementedError()

    def disconnect(self) -> None:
        raise NotImplementedError()

    def initialize_device_from_db(self) -> None:
        raise NotImplementedError()

    def df(self, list_of_points: List[str], force_read: bool = True) -> pd.DataFrame:
        """
        Build a pandas DataFrame from a list of points.  DataFrames are used to present and analyze data.

        :param list_of_points: a list of point names as str
        :returns: pd.DataFrame
        """
        raise NotImplementedError()

    @property
    def simulated_points(self) -> Iterator[Point]:
        """
        iterate over simulated points

        :returns: points if simulated (out_of_service == True)
        :rtype: BAC0.core.devices.Points.Point
        """
        for each in self.points:
            if each.properties.simulated[0]:
                yield each

    async def _buildPointList(self) -> None:
        """
        Read all points from a device into a (Pandas) dataframe (Pandas).  Items are
        accessible by point name.
        """
        raise NotImplementedError()

    def __getitem__(
        self, point_name: Union[str, List[str]]
    ) -> Union[Point, pd.DataFrame]:
        """
        Get a point from its name.
        If a list is passed - a dataframe is returned.

        :param point_name: (str) name of the point or list of point_names
        :type point_name: str
        :returns: (Point) the point (can be Numeric, Boolean or Enum) or pd.DataFrame
        """
        raise NotImplementedError()

    def __iter__(self) -> Point:
        """
        When iterating a device, iterate points of it.
        """
        raise NotImplementedError()

    def __contains__(self, value: str) -> bool:
        "When using in..."
        raise NotImplementedError()

    @property
    def points_name(self) -> List[str]:
        """
        When iterating a device, iterate points of it.
        """
        raise NotImplementedError()

    def to_excel(self) -> None:
        """
        Using xlwings, make a dataframe of all histories and save it
        """
        raise NotImplementedError()

    def __setitem__(self, point_name: str, value: float) -> None:
        """
        Write, sim or ovr value

        :param point_name: Name of the point to set
        :param value: value to write to the point
        :type point_name: str
        :type value: float
        """
        raise NotImplementedError()

    def __len__(self) -> int:
        """
        Will return number of points available
        """
        raise NotImplementedError()

    def _parseArgs(self, arg: str) -> Tuple[str, str]:
        """
        Given a string, interpret the last word as the value, everything else is
        considered to be the point name.
        """
        args = arg.split()
        pointName = " ".join(args[:-1])
        value = args[-1]
        return (pointName, value)

    def clear_histories(self) -> None:
        for point in self.points:
            point.clear_history()

    def update_history_size(self, size: Optional[int] = None) -> None:
        for point in self.points:
            point.properties.history_size = size

    @property
    def analog_units(self) -> Dict[str, str]:
        raise NotImplementedError()

    @property
    def temperatures(self) -> Dict[str, str]:
        raise NotImplementedError()

    @property
    def percent(self) -> Dict[str, str]:
        raise NotImplementedError()

    @property
    def multi_states(self) -> Dict[str, str]:
        raise NotImplementedError()

    @property
    def binary_states(self) -> Dict[str, str]:
        raise NotImplementedError()

    def _findPoint(self, name: str, force_read: bool = True) -> Point:
        """
        Helper that retrieve point based on its name.

        :param name: (str) name of the point
        :param force_read: (bool) read value of the point each time the function is called.
        :returns: Point object
        :rtype: BAC0.core.devices.Point.Point (NumericPoint, EnumPoint or BooleanPoint)
        """
        raise NotImplementedError()

    def find_point(self, objectType: str, objectAddress: float) -> Point:
        """
        Find point based on type and address
        """
        for point in self.points:
            if (
                point.properties.type == objectType
                and float(point.properties.address) == objectAddress
            ):
                return point
        raise ValueError(
            "{} {} doesn't exist in controller".format(objectType, objectAddress)
        )

    def find_overrides(self, force: bool = False) -> None:
        if self._find_overrides_running and not force:
            self._log.warning(
                "Already running ({:.1%})... please wait.".format(
                    self._find_overrides_progress
                )
            )
            return
        lst = []
        self._find_overrides_progress = 0.0
        self._find_overrides_running = True
        total = len(self.points)

        def _find_overrides() -> None:
            self._log.warning(
                "Overrides are being checked, wait for completion message."
            )
            for idx, point in enumerate(self.points):
                if point.is_overridden:
                    lst.append(point)
                self._find_overrides_progress = idx / total
            self._log.warning(
                "Override check ready, results available in device.properties.points_overridden"
            )
            self.properties.points_overridden = lst
            self._find_overrides_running = False
            self._find_overrides_progress = 1.0

        self.do(_find_overrides)

    def find_overrides_progress(self) -> float:
        return self._find_overrides_progress

    def release_all_overrides(self, force: bool = False) -> None:
        if self._release_overrides_running and not force:
            self._log.warning(
                "Already running ({:.1%})... please wait.".format(
                    self._release_overrides_progress
                )
            )
            return
        self._release_overrides_running = True
        self._release_overrides_progress = 0.0

        def _release_all_overrides() -> None:
            self.find_overrides()
            while self._find_overrides_running:
                self._release_overrides_progress = self._find_overrides_progress * 0.5

            if self.properties.points_overridden:
                total = len(self.properties.points_overridden)
                self._log.info("=================================")
                self._log.info("Overrides found... releasing them")
                self._log.info("=================================")
                for idx, point in enumerate(self.properties.points_overridden):
                    self._log.info("Releasing {}".format(point))
                    point.release_ovr()
                    self._release_overrides_progress = (idx / total) / 2 + 0.5
            else:
                self._log.info("No override found")

            self._release_overrides_running = False
            self._release_overrides_progress = 1

        self.do(_release_all_overrides)

    def do(self, func: Any) -> None:
        DoOnce(func).start()

    def __repr__(self) -> str:
        return "{} / Undefined".format(self.properties.name)


def device(*args: Any, **kwargs: Any) -> Device:
    dev = Device(*args, **kwargs)
    t = asyncio.create_task(dev.new_state(DeviceDisconnected))
    while not t.done:
        pass
    return dev


# @fix_docs
class DeviceConnected(Device):
    """
    Find a device on the BACnet network.  Set its state to 'connected'.
    Once connected, all subsequent commands use this BACnet connection.
    """

    async def _init_state(self):
        await self._buildPointList()
        self.properties.network.register_device(self)

    async def disconnect(self, save_on_disconnect=True, unregister=True):
        self._log.info("Wait while stopping polling")
        self.poll(command="stop")
        if unregister:
            self.properties.network.unregister_device(self)
            self.properties.network = None
        if save_on_disconnect:
            self.save()
        if self.properties.db_name:
            await self.new_state(DeviceFromDB)
        else:
            await self.new_state(DeviceDisconnected)

    async def connect(self, *, db=None):
        """
        A connected device can be switched to 'database mode' where the device will
        not use the BACnet network but instead obtain its contents from a previously
        stored database.
        """
        if db:
            self.poll(command="stop")
            self.properties.db_name = db.split(".")[0]
            await self.new_state(DeviceFromDB)
        else:
            self._log.warning(
                "Already connected, provide db arg if you want to connect to db"
            )

    def df(self, list_of_points, force_read=True):
        """
        When connected, calling DF should force a reading on the network.
        """

        his = []
        for point in list_of_points:
            try:
                his.append(self._findPoint(point, force_read=force_read).history)
            except ValueError as ve:
                self._log.error("{}".format(ve))
                continue
        if not _PANDAS:
            return dict(zip(list_of_points, his))
        return pd.DataFrame(dict(zip(list_of_points, his)))

    async def _buildPointList(self):
        """
        Upon connection to build the device point list and properties.
        """
        try:
            self.properties.pss.value = await self.properties.network.read(
                "{} device {} protocolServicesSupported".format(
                    self.properties.address, self.properties.device_id
                )
            )

        except NoResponseFromController as error:
            self._log.error("Controller not found, aborting. ({})".format(error))
            return ("Not Found", "", [], [])

        except SegmentationNotSupported:
            self._log.warning("Segmentation not supported")
            self.segmentation_supported = False
            await self.new_state(DeviceDisconnected)

        self.properties.name = await self.properties.network.read(
            "{} device {} objectName".format(
                self.properties.address, self.properties.device_id
            )
        )
        self.properties.vendor_id = await self.properties.network.read(
            "{} device {} vendorIdentifier".format(
                self.properties.address, self.properties.device_id
            )
        )
        self._log.info(
            "Device {}:[{}] found... building points list".format(
                self.properties.device_id, self.properties.name
            )
        )
        try:
            (
                self.properties.objects_list,
                self.points,
                self._list_of_trendlogs,
            ) = await self._discoverPoints(self.custom_object_list)
            if self.properties.pollDelay is not None and self.properties.pollDelay > 0:
                self.poll(delay=self.properties.pollDelay)
            self.update_history_size(size=self.properties.history_size)
            # self.clear_histories()
        except NoResponseFromController:
            self._log.error("Cannot retrieve object list, disconnecting...")
            self.segmentation_supported = False
            await self.new_state(DeviceDisconnected)
        except IndexError:
            if self._reconnect_on_failure:
                self._log.error("Device creation failed... re-connecting")
                await self.new_state(DeviceDisconnected)
            else:
                self._log.error("Device creation failed... disconnecting")

    def __getitem__(self, point_name):
        """
        Allows the syntax: device['point_name'] or device[list_of_points]

        If calling a list, last value will be used (won't read on the network)
        for performance reasons.
        If calling a simple point, point will be read via BACnet.
        """
        try:
            if isinstance(point_name, list):
                return self.df(point_name, force_read=False)
            elif isinstance(point_name, tuple):
                _type, _address = point_name
                for point in self.points:
                    if point.properties.type == _type and str(
                        point.properties.address
                    ) == str(_address):
                        return point
            else:
                try:
                    return self._findPoint(point_name, force_read=False)
                except ValueError:
                    try:
                        return self._findTrend(point_name)
                    except ValueError:
                        try:
                            if "@prop_" in point_name:
                                point_name = point_name.split("prop_")[1]
                                return self.read_property(
                                    ("device", self.properties.device_id, point_name)
                                )
                            else:
                                raise ValueError()
                        except ValueError:
                            raise ValueError()
        except ValueError as ve:
            self._log.error("{}".format(ve))

    def __iter__(self):
        yield from self.points

    def __contains__(self, value):
        """
        Allows the syntax:
            if "point_name" in device:
        """
        return value in self.points_name

    @property
    def pollable_points_name(self):
        for each in self.points:
            if not isinstance(each, VirtualPoint):
                yield each.properties.name
            else:
                continue

    @property
    def points_name(self):
        for each in self.points:
            yield each.properties.name

    def __setitem__(self, point_name, value):
        """
        Allows the syntax:
            device['point_name'] = value
        """
        try:
            asyncio.create_task(self._findPoint(point_name)._set(value))
        except WritePropertyException as ve:
            self._log.error("{}".format(ve))

    def __len__(self):
        """
        Length of a device = number of points
        """
        return len(self.points)

    def _parseArgs(self, arg):
        args = arg.split()
        pointName = " ".join(args[:-1])
        value = args[-1]
        return (pointName, value)

    @property
    def analog_units(self):
        """
        Shortcut to retrieve all analog points units [Used by Bokeh trending feature]
        """
        au = []
        us = []
        for each in self.points:
            if isinstance(each, NumericPoint):
                au.append(each.properties.name)
                us.append(each.properties.units_state)
        return dict(zip(au, us))

    @property
    def temperatures(self):
        for each in self.analog_units.items():
            if "deg" in each[1]:
                yield each

    @property
    def percent(self):
        for each in self.analog_units.items():
            if "percent" in each[1]:
                yield each

    @property
    def multi_states(self):
        ms = []
        us = []
        for each in self.points:
            if isinstance(each, EnumPoint):
                ms.append(each.properties.name)
                us.append(each.properties.units_state)
        return dict(zip(ms, us))

    @property
    def binary_states(self):
        bs = []
        us = []

        for each in self.points:
            if isinstance(each, BooleanPoint):
                bs.append(each.properties.name)
                us.append(each.properties.units_state)
        return dict(zip(bs, us))

    def _findPoint(self, name, force_read=False):
        """
        Used by getter and setter functions
        """
        for point in self.points:
            if point.properties.name == name:
                if force_read:
                    point.value
                return point
        raise ValueError("{} doesn't exist in controller".format(name))

    def _trendlogs(self):
        for k, v in self._list_of_trendlogs.items():
            name, trendlog = v
            yield trendlog

    @property
    def trendlogs_names(self):
        for each in self._trendlogs():
            yield each.properties.object_name

    @property
    def trendlogs(self):
        return list(self._trendlogs())

    def _findTrend(self, name):
        for trend in self._trendlogs():
            if trend.properties.object_name == name:
                return trend
        raise ValueError("{} doesn't exist in controller".format(name))

    async def read_property(self, prop):
        # if instance == -1:
        #    pass
        if isinstance(prop, tuple):
            _obj, _instance, _prop = prop
        elif isinstance(prop, str):
            _obj = "device"
            _instance = self.properties.device_id
            _prop = prop
        else:
            raise ValueError(
                "Please provide property using tuple with object, instance and property"
            )
        try:
            request = "{} {} {} {}".format(
                self.properties.address, _obj, _instance, _prop
            )
            val = await self.properties.network.read(
                request, vendor_id=self.properties.vendor_id
            )
        except KeyError as error:
            raise Exception("Unknown property : {}".format(error))
        return val

    async def write_property(self, prop, value, priority=None):
        if prop == "description":
            self.update_description(value)
        else:
            if priority is not None:
                priority = "- {}".format(priority)
            if isinstance(prop, tuple):
                _obj, _instance, _prop = prop
            else:
                raise ValueError(
                    "Please provide property using tuple with object, instance and property"
                )
            try:
                request = "{} {} {} {} {} {}".format(
                    self.properties.address, _obj, _instance, _prop, value, priority
                )
                val = await self.properties.network.write(
                    request, vendor_id=self.properties.vendor_id
                )
            except KeyError as error:
                raise Exception("Unknown property : {}".format(error))
            return val

    async def update_bacnet_properties(self):
        """
        Retrieve bacnet properties for this device

        """
        try:
            res = await self.properties.network.readMultiple(
                "{} device {} all".format(
                    self.properties.address, str(self.properties.device_id)
                ),
                vendor_id=self.properties.vendor_id,
                show_property_name=True,
            )
            for each in res:
                if not each:
                    continue
                v, prop = each
                self.properties.bacnet_properties[prop] = v

        except Exception as e:
            raise Exception("Problem reading : {} | {}".format(self.properties.name, e))

    def _bacnet_properties(self, update=False):
        if not self.properties.bacnet_properties or update:
            self.update_bacnet_properties()
        return self.properties.bacnet_properties

    @property
    def bacnet_properties(self):
        return self._bacnet_properties(update=True)

    async def update_description(self, value):
        await self.properties.network.send_text_write_request(
            addr=self.properties.address,
            obj_type="device",
            obj_inst=int(self.device_id),
            value=value,
            prop_id="description",
        )
        self.properties.description = await self.read_property("description")

    async def ping(self):
        try:
            if await self.read_property("objectName") == self.properties.name:
                self.properties.ping_failures = 0
                return True
            else:
                self.properties.ping_failures += 1
                return False
        except NoResponseFromController as e:
            self._log.error(
                "{} ({})| Ping failure ({}).".format(
                    self.properties.name, self.properties.address, e
                )
            )
            self.properties.ping_failures += 1
            return False

    def __repr__(self):
        return "{} / Connected".format(self.properties.name)


# ------------------------------------------------------------------------------


class RPDeviceConnected(DeviceConnected, ReadProperty):
    """
    [Device state] If device is connected but doesn't support ReadPropertyMultiple

    BAC0 will not poll such points automatically (since it would cause excessive network traffic).
    Instead manual polling must be used as needed via the poll() function.
    """

    def __str__(self):
        return "connected [for ReadProperty]"


class RPMDeviceConnected(DeviceConnected, ReadPropertyMultiple):
    """
    [Device state] If device is connected and supports ReadPropertyMultiple
    """

    def __str__(self):
        return "connected [for ReadPropertyMultiple]"


# @fix_docs
class DeviceDisconnected(Device):
    """
    [Device state] Initial state of a device. Disconnected from BACnet.
    """

    async def _init_state(self):
        await self.connect()

    async def connect(self, *, db=None, network=None):
        """
        Attempt to connect to device.  If unable, attempt to connect to a controller database
        (so the user can use previously saved data).
        """
        if network:
            self.properties.network = network
        if not self.properties.network:
            self._log.debug("No network...calling DeviceFromDB")
            if db:
                await self.new_state(DeviceFromDB)
            self._log.info(
                'You can reconnect to network using : "device.connect(network=bacnet)"'
            )

        else:
            try:
                await self.properties.network.read(
                    "{} device {} objectName".format(
                        self.properties.address, self.properties.device_id
                    )
                )

                segmentation = await self.properties.network.read(
                    "{} device {} segmentationSupported".format(
                        self.properties.address, self.properties.device_id
                    )
                )

                self.segmentation_supported = (
                    False if segmentation.numerator == 3 else True
                )

                if self.segmentation_supported:
                    await self.new_state(RPMDeviceConnected)
                else:
                    await self.new_state(RPDeviceConnected)

            except SegmentationNotSupported:
                self.segmentation_supported = False
                self._log.warning(
                    "Segmentation not supported.... expect slow responses."
                )
                await self.new_state(RPDeviceConnected)

            except (NoResponseFromController, AttributeError) as error:
                self._log.warning("Error connecting: %s", error)
                if self.properties.db_name:
                    await self.new_state(DeviceFromDB)
                else:
                    self._log.warning(
                        "Offline: provide database name to load stored data."
                    )
                    self._log.warning("Ex. controller.connect(db = 'backup')")

    def df(self, list_of_points, force_read=True):
        raise DeviceNotConnected("Must connect to BACnet or database")

    @property
    def simulated_points(self):
        for each in self.points:
            if each.properties.simulated:
                yield each

    def _buildPointList(self):
        raise DeviceNotConnected("Must connect to BACnet or database")

    # This should be a "read" function and rpm defined in state rpm
    def read_multiple(
        self, points_list, *, points_per_request=25, discover_request=(None, 6)
    ):
        raise DeviceNotConnected("Must connect to BACnet or database")

    def poll(self, command="start", *, delay=10):
        raise DeviceNotConnected("Must connect to BACnet or database")

    def __getitem__(self, point_name):
        raise DeviceNotConnected("Must connect to BACnet or database")

    def __iter__(self):
        raise DeviceNotConnected("Must connect to BACnet or database")

    def __contains__(self, value):
        raise DeviceNotConnected("Must connect to BACnet or database")

    @property
    def points_name(self):
        raise DeviceNotConnected("Must connect to BACnet or database")

    def to_excel(self):
        raise DeviceNotConnected("Must connect to BACnet or database")

    def __setitem__(self, point_name, value):
        raise DeviceNotConnected("Must connect to BACnet or database")

    def __len__(self):
        raise DeviceNotConnected("Must connect to BACnet or database")

    @property
    def analog_units(self):
        raise DeviceNotConnected("Must connect to BACnet or database")

    @property
    def temperatures(self):
        raise DeviceNotConnected("Must connect to BACnet or database")

    @property
    def percent(self):
        raise DeviceNotConnected("Must connect to BACnet or database")

    @property
    def multi_states(self):
        raise DeviceNotConnected("Must connect to bacnet or database")

    @property
    def binary_states(self):
        raise DeviceNotConnected("Must connect to BACnet or database")

    def _discoverPoints(self, custom_object_list=None):
        raise DeviceNotConnected("Must connect to BACnet or database")

    def _findPoint(self, name, force_read=True):
        raise DeviceNotConnected("Must connect to BACnet or database")

    def __repr__(self):
        return "{} / Disconnected".format(self.properties.name)


# ------------------------------------------------------------------------------

# @fix_docs


class DeviceFromDB(DeviceConnected):
    """
    [Device state] Where requests for a point's present value returns the last
    valid value from the point's history.
    """

    async def _init_state(self):
        try:
            await self.initialize_device_from_db()
        except ValueError as e:
            self._log.error("Problem with DB initialization : {}".format(e))
            # self.new_state(DeviceDisconnected)
            raise

    async def connect(self, *, network=None, from_backup=None):
        """
        In DBState, a device can be reconnected to BACnet using:
            device.connect(network=bacnet) (bacnet = BAC0.connect())
        """
        if network and from_backup:
            raise WrongParameter("Please provide network OR from_backup")

        elif network:
            self._log.debug("Network provided... trying to connect")
            self.properties.network = network
            try:
                name = await self.properties.network.read(
                    "{} device {} objectName".format(
                        self.properties.address, self.properties.device_id
                    )
                )

                segmentation = await self.properties.network.read(
                    "{} device {} segmentationSupported".format(
                        self.properties.address, self.properties.device_id
                    )
                )
                segmentation_supported = False if segmentation.numerator == 3 else True

                if name:
                    if segmentation_supported:
                        self._log.debug("Segmentation supported, connecting...")
                        await self.new_state(RPMDeviceConnected)
                    else:
                        self._log.debug("Segmentation not supported, connecting...")
                        await self.new_state(RPDeviceConnected)
                    # self.db.close()

            except NoResponseFromController:
                self._log.error("Unable to connect, keeping DB mode active")

        else:
            self._log.debug("Not connected, open DB")
            if from_backup:
                self.properties.db_name = from_backup.split(".")[0]
            await self._init_state()

    async def initialize_device_from_db(self):
        self._log.info("Initializing DB")
        # Save important properties for reuse
        if self.properties.db_name:
            dbname = self.properties.db_name
        else:
            self._log.info("Missing argument DB")
            raise ValueError("Please provide db name using device.load_db('name')")

        # network = self.properties.network
        pss = self.properties.pss

        self._props = self.read_dev_prop(self.properties.db_name)
        self.points = []
        for point in self.points_from_sql(self.properties.db_name):
            try:
                self.points.append(OfflinePoint(self, point))
            except RemovedPointException:
                continue

        self.properties = DeviceProperties()
        self.properties.db_name = dbname
        self.properties.address = self._props["address"]
        self.properties.device_id = self._props["device_id"]
        self.properties.network = None
        self.properties.pollDelay = self._props["pollDelay"]
        self.properties.name = self._props["name"]
        self.properties.objects_list = self._props["objects_list"]
        self.properties.pss = pss
        self.properties.serving_chart = {}
        self.properties.charts = []
        self.properties.multistates = self._props["multistates"]
        self.properties.auto_save = self._props["auto_save"]
        self.properties.save_resampling = self._props["save_resampling"]
        self.properties.clear_history_on_save = self._props["clear_history_on_save"]
        self.properties.default_history_size = self._props["history_size"]
        self._log.info("Device restored from db")
        self._log.info(
            'You can reconnect to network using : "device.connect(network=bacnet)"'
        )

    @property
    def simulated_points(self):
        raise DeviceNotConnected("Must connect to BACnet or database")

    def _buildPointList(self):
        raise DeviceNotConnected("Must connect to BACnet or database")

    # This should be a "read" function and rpm defined in state rpm
    def read_multiple(
        self, points_list, *, points_per_request=25, discover_request=(None, 6)
    ):
        raise DeviceNotConnected("Must connect to BACnet or database")

    def poll(self, command="start", *, delay=10):
        raise DeviceNotConnected("Must connect to BACnet or database")

    def __contains__(self, value):
        raise DeviceNotConnected("Must connect to BACnet or database")

    def to_excel(self):
        raise DeviceNotConnected("Must connect to BACnet or database")

    def __setitem__(self, point_name, value):
        raise DeviceNotConnected("Must connect to BACnet or database")

    def _discoverPoints(self, custom_object_list=None):
        raise DeviceNotConnected("Must connect to BACnet or database")

    def __repr__(self):
        return "{} / Disconnected".format(self.properties.name)


# ------------------------------------------------------------------------------


class DeviceLoad(DeviceFromDB):
    def __init__(self, filename=None):
        if filename:
            Device.__init__(self, None, None, None, from_backup=filename)
        else:
            raise Exception("Please provide backup file as argument")
