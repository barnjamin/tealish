from algosdk.abi import ABIType
from .expression_nodes import Bytes, BaseNode


"""
Ints
-----

# should all be 255
int my_int = abi_decode("uint64", 0x00000000000000FF)
int my_int = abi_decode("uint32", 0x000000FF)
int my_int = abi_decode("uint16", 0x00FF)

int my_int = 255
# 0x00000000000000FF
bytes my_encoded_int = abi_encode("uint64", my_int)
# 0x000000FF
bytes my_encoded_int = abi_encode("uint32", my_int)
# 0x00FF
bytes my_encoded_int = abi_encode("uint16", my_int)

assert abi_decode("uint64", abi_encode("uint64", 123)) == 123

Strings
--------

# the abi encoded string 'AB', uint16 len + bytes
# mystring should be == "AB"
bytes my_str = abi_decode("string" 0x00026566)
assert my_str == 0x6566
assert abi_encode("string", my_str) == 0x00026566

Tuples
------
# todo: rn the fields are _just_ int/bytes, how do we specify from abi types?
struct custom_struct
    a: int `abi:"uint16"`
    b: int `abi:"uint16"`
end

custom_struct my_struct = abi_decode(abi_tuple(custom_struct), 0x00FF0001)
assert my_struct.a == 255
assert my_struct.b == 1 


Static Arrays
--------------

# static array of ["A", "B"] should decode to 2 byte strings 
[2]bytes my_str_array = abi_decode("[2]string", 0x000165000166)

Dynamic Arrays
---------------

# For dynamic arrays of dynamic contents
struct avm_dynamic_array<string>:
    # uint16 offsets 
    positions: byte[2][]

    # the contents of the array
    elems: bytes
end

# Dynamic Array of ["A", "B", "C"] 
avm_dynamic_array my_dyn_arr = abi_decode("[]string", 0x00030006000900c000165000166000167)

# len is size of `positions` array / 2 (for uint16 repr)
assert len(my_dyn_arr) == 3 

# under the covers, this finds the right entry in the positions
# array, and performs something like Extract(self.elems, self.positions[i], self.positions[i+1])
assert my_dyn_arr[1] == "B"

# add new entry to positions array with the value the length of 
# the elems bytes prior to appending as a uint16
# append the new bytes to the elems bytes 
my_dyn_arr = append(my_dyn_arr, "D")

assert len(my_dyn_arr) == 4
assert my_dyn_arr[3] == "D"

bytes abi_encoded_dyn_arr = abi_encode("[]string", my_dyn_arr)
assert abi_encoded_dyn_arr == 0x00040009000c000f0012000165000166000167000168


# For dynamic arrays of static contents
struct avm_dynamic_array<uint64>:
    # we can rely on stride rather than tracking offsets
    stride: 8
    # the contents of the array
    elems: bytes
end




"""


class TealishABIType:
    def __init__(self, type_spec: str):
        # Get the type spec from the abi
        self.sdk_type = ABIType.from_string(type_spec)

    def encode(self, val: BaseNode) -> Bytes:
        # from the `sdk_type`, figure out how to
        # encode the value using TEAL
        pass

    def decode(self, val: Bytes) -> BaseNode:
        # from the `sdk_type`, figure out how to
        # decode the value using TEAL
        pass
