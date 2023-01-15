from enum import Enum
from typing import Dict, Tuple, Union, TYPE_CHECKING

if TYPE_CHECKING:
    from .nodes import Struct


class AVMType(str, Enum):
    """AVMType enum represents the possible types an opcode accepts or returns"""

    any = "any"
    bytes = "bytes"
    int = "int"
    none = ""


structs: Dict[str, "Struct"] = {}


def define_struct(struct_name: str, struct: "Struct") -> None:
    structs[struct_name] = struct


def get_struct(struct_name: str) -> "Struct":
    return structs[struct_name]


on_completes: Dict[str, Tuple[AVMType, int]] = {
    "NoOp": (AVMType.int, 0),
    "OptIn": (AVMType.int, 1),
    "CloseOut": (AVMType.int, 2),
    "ClearState": (AVMType.int, 3),
    "UpdateApplication": (AVMType.int, 4),
    "DeleteApplication": (AVMType.int, 5),
}

txn_types: Dict[str, Tuple[AVMType, int]] = {
    "Pay": (AVMType.int, 1),
    "Keyreg": (AVMType.int, 2),
    "Acfg": (AVMType.int, 3),
    "Axfer": (AVMType.int, 4),
    "Afrz": (AVMType.int, 5),
    "Appl": (AVMType.int, 6),
    "Stpf": (AVMType.int, 7),
}

constants: Dict[str, Tuple[AVMType, Union[str, bytes, int]]] = {
    **on_completes,
    **txn_types,
}
