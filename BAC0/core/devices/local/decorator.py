from functools import wraps

from bacpypes3.basetypes import EngineeringUnits
from bacpypes3.local.cmd import Commandable
from bacpypes3.local.oos import OutOfService
from bacpypes3.object import TrendLogObject
from bacpypes3.primitivedata import CharacterString

_SHOULD_BE_COMMANDABLE = ["relinquishDefault", "outOfService", "lowLimit", "highLimit"]

"""
Template

Decorators is an effort to handle object creation without explicitly declare a new class
depending on the properties or features required.



# Usage

## bacnet_properties
This decorator takes a dict as argument defining supplmental properties

## bacnet_property
This decorator takes a simple property and its default value, adds it to object

## Commandable
This decoratore will modify the base class and create a new class that inherit from _commando (see local.object.py)

## Add feature
This decorator works the same than commandable. Could serve as a way to add behaviour like MinOnOff, events, limits, etc...

## Example::

    properties = {"outOfService" : False,
                "relinquishDefault" : 0,
                "units": "degreesCelsius",
                "highLimit": 98}

    @bacnet_properties(properties)
    @commandable()
    def av(instance, objectName, presentValue, description):
        OBJECT_TYPE = AnalogValueObject
        return create(OBJECT_TYPE,instance, objectName, presentValue, description)

    @add_feature(MinOnOff)
    @commandable()
    def bv(instance, objectName, presentValue, description):
        OBJECT_TYPE = BinaryValueObject
        return create(OBJECT_TYPE,instance, objectName, presentValue, description)

    @commandable()
    def datepattern(instance, objectName, presentValue, description):
        OBJECT_TYPE = DatePatternValueObject
        return create(OBJECT_TYPE,instance, objectName, presentValue, description)

    ### The creation takes place when the functions are called
    a = av(1,'AnalogValueName',10,'AnalogValue Description')
    b = bv(1,'BV Name','inactive','BinaryValue Description')
    c = datepattern(1,'My Date Pattern',None,'DatePattern Description')

"""


def _allowed_prop(obj):
    allowed_prop = {}
    #print(obj.propertyList)
    for each in obj.propertyList:
        allowed_prop[each.identifier] = each.get_datatype()
    for base in obj.__bases__:
        try:
            for each in base.propertyList:
                allowed_prop[each.identifier] = each.get_datatype()
        except AttributeError:
            pass
    return allowed_prop


def _mutable(property_name, force_mutable=False):
    if property_name in _SHOULD_BE_COMMANDABLE and not force_mutable:
        mutable = True
    elif force_mutable:
        mutable = force_mutable
    else:
        mutable = False
    return mutable


def make_commandable():
    def decorate(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if callable(func):
                obj = func(*args, **kwargs)
            else:
                obj = func
            # allowed_prop = _allowed_prop(obj)
            _type = obj.get_property_type("presentValue")
            base_cls = obj.__class__
            base_cls_name = obj.__class__.__name__ + "Cmd"
            new_type = type(base_cls_name, (base_cls, Commandable), {})
            new_type.__name__ = base_cls_name
            # register_object_type(new_type, vendor_id=842)
            objectType, instance, objectName, presentValue, description = args
            new_object = new_type(
                objectIdentifier=(base_cls.objectType, instance),
                objectName="{}".format(objectName),
                presentValue=presentValue,
                description=CharacterString("{}".format(description)),
            )
            return new_object

        return wrapper

    return decorate


def make_outOfService():
    def decorate(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if callable(func):
                obj = func(*args, **kwargs)
            else:
                obj = func
            base_cls = obj.__class__
            base_cls_name = obj.__class__.__name__ + "Cmd"
            new_type = type(base_cls_name, (base_cls, OutOfService), {})
            new_type.__name__ = base_cls_name
            # register_object_type(new_type, vendor_id=842)
            objectType, instance, objectName, presentValue, description = args
            new_object = new_type(
                objectIdentifier=(base_cls.objectType, instance),
                objectName="{}".format(objectName),
                presentValue=presentValue,
                description=CharacterString("{}".format(description)),
            )
            return new_object

        return wrapper

    return decorate


def add_feature(cls):
    def decorate(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if callable(func):
                obj = func(*args, **kwargs)
            else:
                obj = func
            base_cls = obj.__class__
            base_cls_name = obj.__class__.__name__ + cls.__name__
            new_type = type(base_cls_name, (cls, base_cls), {})
            instance, objectName, presentValue, description = args
            new_object = new_type(
                objectIdentifier=(base_cls.objectType, instance),
                objectName="{}".format(objectName),
                presentValue=presentValue,
                description=CharacterString("{}".format(description)),
            )
            return new_object

        return wrapper

    return decorate


def bacnet_properties(properties):
    """
    Given a dict of properties, add them to the object
    """

    def decorate(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if callable(func):
                obj = func(*args, **kwargs)
            else:
                obj = func
            #allowed_prop = _allowed_prop(obj)

            for property_name, value in properties.items():
                if property_name == "units":
                    new_prop = EngineeringUnits(value)
                    #obj.units = new_prop
                    obj.__setattr__("units", new_prop)
                else:
                    try:
                        #mutable = _mutable(property_name)
                        #new_prop = Property(
                        #    property_name,
                        #    allowed_prop[property_name],
                        #    default=value,
                        #    mutable=mutable,
                        #)
                        property_type = obj.get_property_type(property_name)
                        print(f"Adding {property_name} of type {property_type} with value {value} to {obj}")
                        obj.__setattr__(property_name, property_type(value))
                    except (KeyError,AttributeError) as error:
                        raise ValueError(
                            f"Invalid property ({property_name}) for object | {error}"
                        )
                    #obj.add_property(new_prop)
            return obj

        return wrapper

    return decorate


def create(object_type, instance, objectName, value, description):
    if object_type is TrendLogObject:
        new_object = object_type(
            objectIdentifier=(object_type.objectType, instance),
            objectName="{}".format(objectName),
            logBuffer=value,
            description=CharacterString("{}".format(description)),
        )
    else:
        new_object = object_type(
            objectIdentifier=(object_type.objectType, instance),
            objectName="{}".format(objectName),
            presentValue=value,
            description=CharacterString("{}".format(description)),
        )
    return new_object
