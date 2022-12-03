import re
import sys
import textwrap
from collections import defaultdict
from enum import Enum
from typing import get_type_hints, Optional, Tuple, List, Dict, Any

from .expressions import Expression, GenericExpression, Literal
from .utils import combine_source_maps, minify_teal


class StackType(str, Enum):
    int = "int"
    bytes = "bytes"
    any = "any"
    none = "none"


line_no: int = 0
level: int = 0
current_output_line: int = 1
output: List[str] = []
source_map: Dict[int, str] = {}


class ParseError(Exception):
    pass


class CompileError(Exception):
    pass


class TealishCompiler:
    def __init__(self, source_lines: List[str]) -> None:
        self.source_lines: List[str] = source_lines
        self.output: List[str] = []
        self.source_map: Dict[int, int] = {}
        self.current_output_line: int = 1
        self.level: int = 0
        self.line_no: int = 0
        self.nodes: List[Node] = []
        self.conditional_count: int = 0
        self.error_messages: dict[int, str] = {}
        self.max_slot: int = 0

    def consume_line(self):
        if self.line_no == len(self.source_lines):
            return
        line = self.source_lines[self.line_no].strip()
        self.line_no += 1
        return line

    def peek(self):
        if self.line_no == len(self.source_lines):
            return
        return self.source_lines[self.line_no].strip()

    def write(self, lines=("",), line_no: int = 0):
        prefix = "  " * self.level
        if type(lines) == str:
            lines = [lines]
        for s in lines:
            self.output.append(prefix + s)
            # print(self.current_output_line, self.output[-1])
            self.source_map[self.current_output_line] = line_no
            self.current_output_line += 1

    def parse(self):
        node = Program.consume(self, None)
        self.nodes.append(node)

    def compile(self):
        if not self.nodes:
            self.parse()
        for node in self.nodes:
            node.visit()
        return self.output

    def traverse(self, node=None, visitor=None):
        if node is None:
            node = self.nodes[0]
        if visitor:
            visitor(node)
        if getattr(node, "nodes", []):
            for n in node.nodes:
                self.traverse(n, visitor)

    def reformat(self):
        if not self.nodes:
            self.parse()
        return self.nodes[0].reformat()


class Scope:
    def __init__(
        self,
        name: str,
        parent: Optional["Scope"] = None,
        slot_range: Optional[Tuple[int, int]] = None,
    ):
        self.parent = parent
        self.slots: Dict[int, StackType] = {}
        self.slot_range = slot_range if slot_range is not None else [0, 200]
        self.consts: Dict[str, StackType] = {}
        self.blocks: Dict[str, Block] = {}
        self.functions: Dict[str, Func] = {}
        self.name = name
        if parent is not None and parent.name:
            self.name = f"{parent.name}__{name}"


class Node:
    pattern = ""
    possible_child_nodes: List[type["Node"]] = []

    def __init__(
        self, line: int, parent: "Node" = None, compiler: TealishCompiler = None
    ) -> None:
        self.parent = parent
        self.current_scope: Optional[Scope] = None
        if parent:
            self.current_scope = parent.current_scope
        self.compiler = compiler
        self.line = line
        self.line_no = compiler.line_no if compiler else 0
        self.nodes: List[Node] = []

        try:
            matches: Optional[re.Match[str]] = re.match(self.pattern, self.line)
            if matches is None:
                raise ParseError(
                    f'Pattern ({self.pattern}) does not match for {self} for line "{self.line}"'
                )
            self.matches = matches.groupdict()
        except AttributeError:
            raise ParseError(
                f'Pattern ({self.pattern}) does not match for {self} for line "{self.line}"'
            )

        type_hints = get_type_hints(self.__class__)
        for name, expr_class in type_hints.items():
            if name in self.matches:
                try:
                    if self.matches[name] is not None and hasattr(expr_class, "parse"):
                        value = expr_class.parse(self.matches[name])
                    else:
                        value = self.matches[name]
                    setattr(self, name, value)
                except Exception as e:
                    raise
                    raise ParseError(str(e) + f" at line {self.compiler.line_no}")

    def add_child(self, node: "Node"):
        if not isinstance(node, tuple(self.possible_child_nodes)):
            raise ParseError(
                f"Unexpected child node {node} in {self} at line {self.line_no}!"
            )
        node.parent = self
        if not node.current_scope:
            node.current_scope = self.current_scope
        self.nodes.append(node)

    @classmethod
    def consume(cls, compiler: TealishCompiler, parent: "Node") -> "Node":
        line = compiler.consume_line()
        return cls(line, parent=parent, compiler=compiler)

    def visit(self):
        try:
            self.process()
        except Exception as e:
            raise CompileError(str(e) + f" at line {self.line_no}")
        self.compiler.level += 1
        for n in self.nodes:
            n.visit()
        self.compiler.level -= 1

    def process(self):
        raise NotImplementedError()

    def write(self, lines: List[str]):
        if self.compiler is None:
            raise Exception("No compiler?")
        self.compiler.write(lines, self.line_no)

    def new_scope(self, name: str = "", slot_range: Tuple[int, int] | None = None):
        parent_scope = self.parent.get_current_scope() if self.parent else None
        self.current_scope = Scope(name, parent_scope, slot_range)

    def get_scope(self) -> dict[str, Any]:
        scope = {
            "slots": {},
            "consts": {},
            "blocks": {},
            "functions": {},
        }
        for s in self.get_scopes():
            scope["consts"].update(s.consts)
            scope["blocks"].update(s.blocks)
            scope["slots"].update(s.slots)
            scope["functions"].update(s.functions)
        return scope

    def get_current_scope(self):
        return self.current_scope

    def get_scopes(self):
        scopes: List[Scope] = []
        s = self.get_current_scope()
        while True:
            scopes.append(s)
            if s.parent is not None:
                s = s.parent
            else:
                break
        return scopes

    def get_const(self, name):
        consts = {}
        for s in self.get_scopes():
            consts.update(s.consts)
        return consts[name]

    def get_slots(self):
        slots = {}
        for s in self.get_scopes():
            slots.update(s.slots)
        return slots

    def get_var(self, name):
        slots = self.get_slots()
        if name in slots:
            return slots[name]
        else:
            return (None, None)

    def declare_var(self, name, type):
        slot, _ = self.get_var(name)
        if slot is not None:
            raise Exception(f'Redefinition of variable "{name}"')
        scope = self.get_current_scope()
        if "func__" in scope.name:
            # If this var is declared in a function then use the global max slot + 1
            # This is to prevent functions using overlapping slots
            slot = self.compiler.max_slot + 1
        else:
            slot = self.find_slot()
        self.compiler.max_slot = max(self.compiler.max_slot, slot)
        scope.slots[name] = [slot, type]
        return slot

    def del_var(self, name):
        scope = self.get_current_scope()
        if name in scope.slots:
            del scope.slots[name]

    def find_slot(self):
        scope = self.get_current_scope()
        min, max = scope.slot_range
        used_slots = [False] * 255
        slots = self.get_slots()
        for k in slots:
            slot = slots[k][0]
            used_slots[slot] = True
        for i, _ in enumerate(used_slots):
            if not used_slots[i]:
                if i >= min and i <= max:
                    return i
        raise Exception("No available slots!")

    def get_blocks(self) -> Dict[str, "Block"]:
        blocks = {}
        for s in self.get_scopes():
            blocks.update(s.blocks)
        return blocks

    def get_block(self, name):
        block = self.get_blocks().get(name)
        return block

    def is_descendant_of(self, node_class):
        return self.find_parent(node_class) is not None

    def find_parent(self, node_class):
        p = self.parent
        while p:
            if isinstance(p, node_class):
                return p
            p = p.parent
        return None

    def has_child_node(self, node_class):
        for node in self.nodes:
            if isinstance(node, node_class) or node.has_child_node(node_class):
                return True
        return False

    def reformat(self):
        raise NotImplementedError(
            f"reformat() not implemented for {self} for line {self.line_no}"
        )

    def __repr__(self):
        return self.__class__.__name__


class Statement(Node):
    @classmethod
    def consume(cls, compiler: TealishCompiler, parent: Node):
        line = compiler.peek()
        if line.startswith("block "):
            return Block.consume(compiler, parent)
        elif line.startswith("switch "):
            return Switch.consume(compiler, parent)
        elif line.startswith("func "):
            return Func.consume(compiler, parent)
        elif line.startswith("if "):
            return IfStatement.consume(compiler, parent)
        elif line.startswith("while "):
            return WhileStatement.consume(compiler, parent)
        elif line.startswith("for "):
            return ForStatement.consume(compiler, parent)
        elif line.startswith("teal:"):
            return Teal.consume(compiler, parent)
        elif line.startswith("inner_group:"):
            return InnerGroup.consume(compiler, parent)
        elif line.startswith("inner_txn:"):
            return InnerTxn.consume(compiler, parent)
        else:
            return LineStatement.consume(compiler, parent)


class Program(Node):
    possible_child_nodes = [Statement]

    def __init__(
        self, line, parent: Node = None, compiler: TealishCompiler = None
    ) -> None:
        super().__init__(line, parent, compiler)
        self.new_scope("")

    def get_current_scope(self):
        return self.current_scope

    @classmethod
    def consume(cls, compiler: TealishCompiler, parent: Node):
        node = Program("", parent=parent, compiler=compiler)
        while True:
            if compiler.peek() is None:
                break
            node.add_child(Statement.consume(compiler, node))
        return node

    def visit(self):
        for n in self.nodes:
            n.visit()

    def reformat(self):
        output = ""
        for n in self.nodes:
            output += n.reformat() + "\n"
        return output


class InlineStatement(Statement):
    pass


class LineStatement(InlineStatement):
    @classmethod
    def consume(cls, compiler: TealishCompiler, parent: Node):
        line = compiler.consume_line()
        if line.startswith("#pragma"):
            if compiler.line_no != 1:
                raise ParseError(
                    f'Teal version must be specified in the first line of the program: "{line}" at {compiler.line_no}.'
                )
            return TealVersion(line, parent, compiler=compiler)
        elif line.startswith("#"):
            return Comment(line, parent, compiler=compiler)
        elif line == "":
            return Blank(line, parent, compiler=compiler)
        elif line.startswith("const "):
            return Const(line, parent, compiler=compiler)
        elif line.startswith("int "):
            return IntDeclaration(line, parent, compiler=compiler)
        elif line.startswith("bytes "):
            return BytesDeclaration(line, parent, compiler=compiler)
        elif line.startswith("jump "):
            return Jump(line, parent, compiler=compiler)
        elif line.startswith("return"):
            return Return(line, parent, compiler=compiler)
        elif " = " in line:
            return Assignment(line, parent, compiler=compiler)
        elif line.startswith("break"):
            return Break(line, parent, compiler=compiler)
        # Statement functions
        elif line.startswith("exit("):
            return Exit(line, parent, compiler=compiler)
        elif line.startswith("assert("):
            return Assert(line, parent, compiler=compiler)
        elif re.match(r"[a-zA-Z_0-9]+\(.*\)", line):
            return FunctionCall(line, parent, compiler=compiler)
        else:
            raise ParseError(
                f'Unexpected line statement: "{line}" at {compiler.line_no}.'
            )

    def reformat(self):
        return self.line


class TealVersion(LineStatement):
    pattern = r"#pragma version (?P<version>\d+)$"
    version: int

    def process(self):
        self.write(f"#pragma version {self.version}")


class Comment(LineStatement):
    pattern = r"#\s*(?P<comment>.*)$"
    comment: str

    def process(self):
        self.write(f"// {self.comment}")


class Blank(LineStatement):
    def process(self):
        self.write("")


class Const(LineStatement):
    pattern = r"const (?P<type>\bint\b|\bbytes\b) (?P<name>[A-Z][a-zA-Z0-9_]*) = (?P<expression>.*)$"
    type: StackType
    name: str
    expression: Literal

    def process(self):
        scope = self.get_current_scope()
        scope.consts[self.name] = (self.type, self.expression.value)


class Jump(LineStatement):
    pattern = r"jump (?P<block_name>.*)$"
    block_name: str

    def process(self):
        self.write(f"// {self.line}")
        b = self.get_block(self.block_name)
        self.write(f"b {b.label}")


class Exit(LineStatement):
    pattern = r"exit\((?P<expression>.*)\)$"
    type: str
    name: str
    expression: GenericExpression

    def process(self):
        self.write(f"// {self.line}")
        self.expression.process(self.get_scope())
        self.write(self.expression.teal())
        self.write("return")


class FunctionCall(LineStatement):
    pattern = r"(?P<expression>[a-zA-Z_0-9]+\(.*\))$"
    expression: GenericExpression

    def process(self):
        self.write(f"// {self.line}")
        self.expression.process(self.get_scope())
        self.write(self.expression.teal())


class Assert(LineStatement):
    pattern = r'assert\((?P<arg>.*?)(, "(?P<message>.*?)")?\)$'
    arg: GenericExpression
    message: str

    def process(self):
        self.arg.process(self.get_scope())
        assert self.arg.type in (
            StackType.int,
            StackType.any,
        ), f"Incorrect type for assert. Expected int, got {self.arg.type}"
        self.write(f"// {self.line}")
        self.write(self.arg.teal())
        if self.message:
            self.compiler.error_messages[self.line_no] = self.message
            self.write(f"assert // {self.message}")
        else:
            self.write("assert")


class BytesDeclaration(LineStatement):
    pattern = r"bytes (?P<name>[a-z][a-zA-Z0-9_]*)( = (?P<expression>.*))?$"
    name: str
    expression: GenericExpression

    def process(self):
        slot = self.declare_var(self.name, StackType.bytes)
        self.write(f"// {self.line} [slot {slot}]")
        if self.expression:
            self.expression.process(self.get_scope())
            assert self.expression.type in (
                StackType.bytes,
                StackType.any,
            ), f"Incorrect type for bytes assignment. Expected bytes, got {self.expression.type}"
            self.write(self.expression.teal())
            self.write(f"store {slot} // {self.name}")


class IntDeclaration(LineStatement):
    pattern = r"int (?P<name>[a-z][a-zA-Z0-9_]*)( = (?P<expression>.*))?$"
    name: str
    expression: GenericExpression

    def process(self):
        slot = self.declare_var(self.name, StackType.int)
        self.write(f"// {self.line} [slot {slot}]")
        if self.expression:
            self.expression.process(self.get_scope())
            assert self.expression.type in (
                StackType.int,
                StackType.any,
            ), f"Incorrect type for int assignment. Expected int, got {self.expression.type}"
            self.write(self.expression.teal())
            self.write(f"store {slot} // {self.name}")


class Assignment(LineStatement):
    pattern = r"(?P<names>([a-z_][a-zA-Z0-9_]*,?\s*)+) = (?P<expression>.*)$"
    names: str
    expression: GenericExpression

    def process(self):
        self.expression.process(self.get_scope())
        self.write(f"// {self.line}")
        self.write(self.expression.teal())
        t = self.expression.type
        types = t if type(t) == list else [t]
        names = [s.strip() for s in self.names.split(",")]
        assert len(types) == len(
            names
        ), f"Incorrect number of names ({len(names)}) for values ({len(types)}) in assignment"
        for i, name in enumerate(names):
            if name == "_":
                self.write("pop // discarding value for _")
            else:
                # TODO: we have types for vars now. We should somehow make sure the expression is the correct type
                slot, t = self.get_var(name)
                if slot is None:
                    raise Exception(f'Var "{name}" not declared in current scope')
                assert (
                    types[i] == "any" or types[i] == t
                ), f"Incorrect type for {t} assignment. Expected {t}, got {types[i]}"
                self.write(f"store {slot} // {name}")


class Block(Statement):
    possible_child_nodes = [Statement]
    pattern = r"block (?P<name>[a-zA-Z_0-9]+):$"
    name: str

    def __init__(self, line, parent=None, compiler=None) -> None:
        super().__init__(line, parent, compiler)
        scope = self.get_current_scope()
        scope.blocks[self.name] = self
        self.label = scope.name + ("__" if scope.name else "") + self.name
        self.new_scope(self.name)

    @classmethod
    def consume(cls, compiler, parent) -> "Block":
        line = compiler.consume_line()
        block = Block(line, parent, compiler=compiler)
        while True:
            if compiler.peek() == "end":
                compiler.consume_line()
                break
            block.add_child(Statement.consume(compiler, block))
        return block

    def process(self):
        self.write(f"// block {self.name}")
        self.write(f"{self.label}:")

    def reformat(self):
        output = ""
        output += self.line + "\n"
        for n in self.nodes:
            output += indent(n.reformat()) + "\n"
        output += "end"
        return output


class SwitchOption(Node):
    pattern = r"(?P<expression>.*): (?P<block_name>.*)"
    expression: GenericExpression
    block_name: str

    def reformat(self):
        return self.line + "\n"


class SwitchElse(Node):
    pattern = r"else: (?P<block_name>.*)"
    block_name: str

    def reformat(self):
        return self.line + "\n"


class Switch(InlineStatement):
    possible_child_nodes = [SwitchOption, SwitchElse]
    pattern = r"switch (?P<expression>.*):$"
    expression: GenericExpression

    def __init__(self, line, parent=None, compiler=None) -> None:
        super().__init__(line, parent, compiler)
        self.options: List[SwitchOption] = []
        self.else_ = None

    def add_option(self, node):
        self.options.append(node)
        self.add_child(node)

    def add_else(self, node):
        self.else_ = node
        self.add_child(node)

    @classmethod
    def consume(cls, compiler, parent):
        switch = Switch(compiler.consume_line(), parent, compiler=compiler)
        while True:
            if compiler.peek() == "end":
                compiler.consume_line()
                break
            if compiler.peek().startswith("else:"):
                switch.add_else(
                    SwitchElse(compiler.consume_line(), switch, compiler=compiler)
                )
            else:
                switch.add_option(
                    SwitchOption(compiler.consume_line(), switch, compiler=compiler)
                )
        return switch

    def visit(self):
        self.write(f"// {self.line}")
        self.expression.process(self.get_scope())
        for i, node in enumerate(self.options):
            node.expression.process(self.get_scope())
            self.write(self.expression.teal())
            self.write(node.expression.teal())
            self.write("==")
            b = self.get_block(node.block_name)
            self.write(f"bnz {b.label}")
        if self.else_:
            b = self.get_block(self.else_.block_name)
            self.write(f"b {b.label} // else")
        else:
            self.write("err // unexpected value")

    def reformat(self):
        output = ""
        output += self.line + "\n"
        for n in self.nodes:
            output += indent(n.reformat())
        output += "end"
        return output


class TealLine(Node):
    def process(self):
        self.write(f"{self.line}")

    def reformat(self):
        return self.line + "\n"


class Teal(InlineStatement):
    possible_child_nodes = [TealLine]

    @classmethod
    def consume(cls, compiler, parent):
        node = Teal(compiler.consume_line(), parent, compiler=compiler)
        while True:
            if compiler.peek() == "end":
                compiler.consume_line()
                break
            node.add_child(TealLine.consume(compiler, node))
        return node

    def reformat(self):
        output = ""
        output += self.line + "\n"
        for n in self.nodes:
            output += indent(n.reformat())
        output += "end"
        return output

    def visit(self):
        for n in self.nodes:
            n.visit()


class InnerTxnFieldSetter(InlineStatement):
    pattern = r"(?P<field_name>.*?)(\[(?P<index>\d\d?)\])?: (?P<expression>.*)"
    field_name: str
    index: int
    expression: GenericExpression

    def reformat(self):
        return self.line + "\n"


class InnerTxn(InlineStatement):
    possible_child_nodes = [InnerTxnFieldSetter]

    @classmethod
    def consume(cls, compiler, parent):
        node = InnerTxn(compiler.consume_line(), parent, compiler=compiler)
        while True:
            if compiler.peek() == "end":
                compiler.consume_line()
                break
            elif compiler.peek().startswith("#"):
                compiler.consume_line()
            else:
                node.add_child(
                    InnerTxnFieldSetter(
                        compiler.consume_line(), node, compiler=compiler
                    )
                )

        # If this InnerTxn is not in a InnerGroup we make it a InnerGroup of 1
        if not cls.is_descendant_of(node, InnerGroup):
            group = InnerGroup("", parent, compiler=compiler)
            group.add_child(node)
            return group
        return node

    def visit(self):
        self.write(f"// {self.line}")
        array_fields = defaultdict(list)
        for i, node in enumerate(self.nodes):
            if node.index is not None:
                index = int(node.index)
                n = len(array_fields[node.field_name])
                if n == index:
                    array_fields[node.field_name].append(node)
                else:
                    raise ParseError(
                        f"Inccorrect field array index {index} (expected {n}) at line {self.compiler.line_no}!"
                    )
            else:
                node.expression.process(self.get_scope())
                self.write(node.expression.teal())
                self.write(f"itxn_field {node.field_name}")
        for a in array_fields.values():
            for node in a:
                node.expression.process(self.get_scope())
                self.write(node.expression.teal())
                self.write(f"itxn_field {node.field_name}")

    def reformat(self):
        output = ""
        output += self.line + "\n"
        for n in self.nodes:
            output += indent(n.reformat())
        output += "end"
        return output


class InnerGroup(InlineStatement):
    possible_child_nodes = [Statement]

    @classmethod
    def consume(cls, compiler, parent):
        node = InnerGroup(compiler.consume_line(), parent, compiler=compiler)
        while True:
            if compiler.peek().startswith("end"):
                compiler.consume_line()
                break
            node.add_child(Statement.consume(compiler, node))
        return node

    def visit(self):
        self.write(f"// {self.line}")
        self.write("itxn_begin")
        for i, node in enumerate(self.nodes):
            node.visit()
            if i < (len(self.nodes) - 1):
                if isinstance(node, InnerTxn) or node.has_child_node(InnerTxn):
                    self.write("itxn_next")
        self.write("itxn_submit")

    def reformat(self):
        if self.line:
            output = ""
            output += self.line + "\n"
            for n in self.nodes:
                output += indent(n.reformat())
            output += "end"
            return output
        else:
            return self.nodes[0].reformat()


class IfThen(Node):
    possible_child_nodes = [InlineStatement]

    @classmethod
    def consume(cls, compiler, parent):
        node = IfThen("", parent, compiler=compiler)
        while True:
            if compiler.peek().startswith(("end", "elif", "else:")):
                break
            node.add_child(InlineStatement.consume(compiler, node))
        return node

    def process(self):
        self.write("// then:")

    def reformat(self):
        output = ""
        output += "\n".join([indent(n.reformat()) for n in self.nodes])
        return output


class Elif(Node):
    possible_child_nodes = [InlineStatement]
    pattern = r"elif ((?P<modifier>not) )?(?P<condition>.*):"
    condition: GenericExpression
    modifier: str

    @classmethod
    def consume(cls, compiler, parent):
        node = Elif(compiler.consume_line(), parent, compiler=compiler)
        while True:
            if compiler.peek().startswith(("end", "elif", "else:")):
                break
            node.add_child(InlineStatement.consume(compiler, node))
        return node

    def process(self):
        self.write(f"// {self.line}")
        self.condition.process(self.get_scope())
        self.write(self.condition.teal())
        if self.modifier == "not":
            self.write(f"bnz {self.next_label}")
        else:
            self.write(f"bz {self.next_label}")

    def reformat(self):
        output = ""
        output += self.line + "\n"
        output += "\n".join([indent(n.reformat()) for n in self.nodes])
        return output


class Else(Node):
    possible_child_nodes = [InlineStatement]
    pattern = r"else:"

    @classmethod
    def consume(cls, compiler, parent):
        node = Else(compiler.consume_line(), parent, compiler=compiler)
        while True:
            if compiler.peek().startswith(("end")):
                break
            node.add_child(InlineStatement.consume(compiler, node))
        return node

    def process(self):
        self.write(f"// {self.line}")

    def reformat(self):
        output = ""
        output += self.line + "\n"
        output += "\n".join([indent(n.reformat()) for n in self.nodes])
        return output


class IfStatement(InlineStatement):
    possible_child_nodes = [IfThen, Elif, Else]
    pattern = r"if ((?P<modifier>not) )?(?P<condition>.*):$"
    condition: GenericExpression
    modifier: str

    def __init__(self, line, parent=None, compiler=None) -> None:
        super().__init__(line, parent, compiler)
        self.if_then = None
        self.elifs: List[Elif] = []
        self.else_ = None
        self.conditional_index = compiler.conditional_count
        compiler.conditional_count += 1
        self.end_label = f"l{self.conditional_index}_end"

    def add_if_then(self, node):
        node.label = ""
        self.if_then = node
        self.add_child(node)

    def add_elif(self, node):
        i = len(self.elifs)
        node.label = f"l{self.conditional_index}_elif_{i}"
        self.elifs.append(node)
        self.add_child(node)

    def add_else(self, node):
        node.label = f"l{self.conditional_index}_else"
        self.else_ = node
        self.add_child(node)

    @classmethod
    def consume(cls, compiler, parent):
        if_statement = IfStatement(compiler.consume_line(), parent, compiler=compiler)
        if_statement.add_if_then(IfThen.consume(compiler, if_statement))
        while True:
            if compiler.peek() == "end":
                compiler.consume_line()
                break
            elif compiler.peek().startswith("elif "):
                if_statement.add_elif(Elif.consume(compiler, if_statement))
            elif compiler.peek().startswith("else:"):
                if_statement.add_else(Else.consume(compiler, if_statement))
        return if_statement

    def visit(self):
        for i, node in enumerate(self.nodes[:-1]):
            node.next_label = self.nodes[i + 1].label
        if len(self.nodes) > 1:
            next_label = self.nodes[1].label
        else:
            next_label = self.end_label
        self.nodes[-1].next_label = self.end_label
        self.write(f"// {self.line}")
        self.compiler.level += 1
        self.condition.process(self.get_scope())
        self.write(self.condition.teal())
        if self.modifier == "not":
            self.write(f"bnz {next_label}")
        else:
            self.write(f"bz {next_label}")
        self.if_then.visit()
        if self.elifs or self.else_:
            self.write(f"b {self.end_label}")
        for i, n in enumerate(self.elifs):
            self.write(f"{n.label}:")
            n.visit()
            if i != (len(self.elifs) - 1) or self.else_:
                self.write(f"b {self.end_label}")
        if self.else_:
            n = self.else_
            self.write(f"{n.label}:")
            n.visit()
        self.write(f"{self.end_label}: // end")
        self.compiler.level -= 1

    def reformat(self):
        output = ""
        output += self.line + "\n"
        for n in self.nodes:
            s = n.reformat()
            if s:
                output += s + "\n"
        output += "end"
        return output


class Break(LineStatement):
    pattern = r"break$"

    def __init__(self, line, parent=None, compiler=None) -> None:
        super().__init__(line, parent, compiler)
        self.parent_loop = self.find_parent(WhileStatement)
        if self.parent_loop is None:
            raise ParseError(
                f'"break" should only be used in a while loop! Line {self.line_no}'
            )

    def process(self):
        self.write(f"// {self.line}")
        self.write(f"b {self.parent_loop.end_label}")


class WhileStatement(InlineStatement):
    possible_child_nodes = [InlineStatement]
    pattern = r"while ((?P<modifier>not) )?(?P<condition>.*):$"
    condition: GenericExpression
    modifier: str

    def __init__(self, line, parent=None, compiler=None) -> None:
        super().__init__(line, parent, compiler)
        self.conditional_index = compiler.conditional_count
        compiler.conditional_count += 1
        self.start_label = f"l{self.conditional_index}_while"
        self.end_label = f"l{self.conditional_index}_end"

    @classmethod
    def consume(cls, compiler, parent):
        node = WhileStatement(compiler.consume_line(), parent, compiler=compiler)
        while True:
            if compiler.peek() == "end":
                compiler.consume_line()
                break
            node.add_child(InlineStatement.consume(compiler, node))
        return node

    def visit(self):
        self.write(f"// {self.line}")
        self.write(f"{self.start_label}:")
        self.compiler.level += 1
        self.condition.process(self.get_scope())
        self.write(self.condition.teal())
        if self.modifier == "not":
            self.write(f"bnz {self.end_label}")
        else:
            self.write(f"bz {self.end_label}")
        for n in self.nodes:
            n.visit()
        self.write(f"b {self.start_label}")
        self.write(f"{self.end_label}: // end")
        self.compiler.level -= 1

    def reformat(self):
        output = ""
        output += self.line + "\n"
        for n in self.nodes:
            s = n.reformat()
            if s:
                output += s + "\n"
        output += "end"
        return output


class ForStatement(InlineStatement):
    possible_child_nodes = [InlineStatement]
    pattern = r"for (?P<var>[a-z_][a-zA-Z0-9_]*) in (?P<start>[a-zA-Z0-9_]+):(?P<end>[a-zA-Z0-9_]+):$"
    var: str
    start: GenericExpression
    end: GenericExpression

    def __init__(self, line, parent=None, compiler=None) -> None:
        super().__init__(line, parent, compiler)
        self.conditional_index = compiler.conditional_count
        compiler.conditional_count += 1
        self.start_label = f"l{self.conditional_index}_for"
        self.end_label = f"l{self.conditional_index}_end"

    @classmethod
    def consume(cls, compiler: TealishCompiler, parent: Node):
        node = ForStatement(compiler.consume_line(), parent, compiler=compiler)
        while True:
            if compiler.peek() == "end":
                compiler.consume_line()
                break
            node.add_child(InlineStatement.consume(compiler, node))
        return node

    def visit(self):
        if self.var == "_":
            self.visit_implicit_counter()
        else:
            self.visit_explicit_counter()

    def visit_explicit_counter(self):
        self.write(f"// {self.line}")
        self.compiler.level += 1
        self.start.process(self.get_scope())
        self.end.process(self.get_scope())
        self.write(self.start.teal())
        slot = self.declare_var(self.var, StackType.int)
        self.write(f"store {slot} // {self.var}")
        self.write(f"{self.start_label}:")
        self.write(f"load {slot} // {self.var}")
        self.write(self.end.teal())
        self.write("==")
        self.write(f"bnz {self.end_label}")
        for n in self.nodes:
            n.visit()
        self.write(f"load {slot} // {self.var}")
        self.write("pushint 1")
        self.write("+")
        self.write(f"store {slot} // {self.var}")
        self.write(f"b {self.start_label}")
        self.write(f"{self.end_label}: // end")
        self.del_var(self.var)
        self.compiler.level -= 1

    def visit_implicit_counter(self):
        self.write(f"// {self.line}")
        self.compiler.level += 1
        self.start.process(self.get_scope())
        self.end.process(self.get_scope())
        self.write(self.start.teal())
        self.write("dup")
        self.write(f"{self.start_label}:")
        self.write(self.end.teal())
        self.write("==")
        self.write(f"bnz {self.end_label}")
        for n in self.nodes:
            n.visit()
        self.write("pushint 1")
        self.write("+")
        self.write("dup")
        self.write(f"b {self.start_label}")
        self.write("pop")
        self.write(f"{self.end_label}: // end")
        self.compiler.level -= 1

    def reformat(self):
        output = ""
        output += self.line + "\n"
        for n in self.nodes:
            s = n.reformat()
            if s:
                output += s + "\n"
        output += "end"
        return output


class ArgsList(Expression):
    arg_pattern = r"(?P<arg_name>[a-z][a-z_0-9]*): (?P<arg_type>int|bytes)"
    pattern = rf"(?P<args>({arg_pattern}(, )?)*)"
    args: List[str]

    def __init__(self, string) -> None:
        super().__init__(string)
        self.args = re.findall(self.arg_pattern, string)


class Func(InlineStatement):
    possible_child_nodes = [InlineStatement]
    pattern = r"func (?P<name>[a-zA-Z_0-9]+)\((?P<args>.*)\)(?P<returns>.*):$"
    name: str
    args: ArgsList
    func_returns: str

    def __init__(self, line, parent=None, compiler=None) -> None:
        super().__init__(line, parent, compiler)
        scope = self.get_current_scope()
        scope.functions[self.name] = self
        self.label = scope.name + "__func__" + self.name
        self.new_scope("func__" + self.name)

        self.func_returns = list(
            filter(None, [s.strip() for s in self.line.split(",")])
        )

    @classmethod
    def consume(cls, compiler, parent):
        func = Func(compiler.consume_line(), parent, compiler=compiler)
        while True:
            if compiler.peek() == "end":
                compiler.consume_line()
                break
            func.add_child(InlineStatement.consume(compiler, func))
        if type(func.nodes[-1]) != Return:
            raise ParseError(
                f"func must end with a return statement at line {compiler.line_no}!"
            )
        return func

    def visit(self):
        self.write(f"// {self.line}")
        self.write(f"{self.label}:")
        for (name, type) in self.args.args[::-1]:
            slot = self.declare_var(name, type)
            self.write(f"store {slot} // {name}")
        for i, node in enumerate(self.nodes):
            node.visit()

    def reformat(self):
        output = ""
        output += self.line + "\n"
        output += "\n".join([indent(n.reformat()) for n in self.nodes])
        output += "\nend"
        return output


class Return(LineStatement):
    pattern = r"return ?(?P<args>.*?)?$"
    args: str

    def __init__(self, line, parent=None, compiler=None) -> None:
        super().__init__(line, parent, compiler)
        if not self.is_descendant_of(Func):
            raise ParseError(
                f'"return" should only be used in a function! Line {self.line_no}'
            )

    def process(self):
        self.write(f"// {self.line}")
        if self.args:
            args = split_return_args(self.args)
            for a in args[::-1]:
                arg = a.strip()
                expression = GenericExpression.parse(arg)
                expression.process(self.get_scope())
                self.write(expression.teal())
        self.write("retsub")


def split_return_args(s):
    parentheses = 0
    quotes = False
    for i in range(len(s)):
        if s[i] == '"':
            quotes = not quotes
        if not quotes:
            if s[i] == "(":
                parentheses += 1
            if s[i] == ")":
                parentheses -= 1
            if parentheses == 0 and s[i] == ",":
                return [s[:i].strip()] + split_return_args(s[i + 1 :].strip())
    return [s]


def compile_program(source, debug=False):
    source_lines = source.split("\n")
    compiler = TealishCompiler(source_lines)
    try:
        compiler.parse()
    except ParseError as e:
        print(e)
        sys.exit(1)
    except Exception:
        print(f"Line: {compiler.line_no}")
        raise
    try:
        compiler.compile()
    except CompileError as e:
        print(e)
        sys.exit(1)
    teal = compiler.output + [""]
    if debug:
        for i in range(0, len(teal)):
            print(" ".join([str(i + 1), str(compiler.source_map[i + 1]), teal[i]]))
    min_teal, teal_source_map = minify_teal(teal)
    _ = combine_source_maps(teal_source_map, compiler.source_map)
    return teal, min_teal, compiler.source_map


def compile_lines(source_lines):
    compiler = TealishCompiler(source_lines)
    compiler.parse()
    compiler.compile()
    teal_lines = compiler.output
    return teal_lines


def indent(s):
    return textwrap.indent(s, "    ")
