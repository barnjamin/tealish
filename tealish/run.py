import json
from typing import Any
from pathlib import Path
import sys
from tealish import *
from tealish.expression_nodes import BinaryOp, Bytes, GlobalField, TxnField, Integer


class TealishExpr:
    node: Node 
    children: list["TealishExpr"]

    def __init__(self, node: Node, children: list["TealishExpr"]):
        self.node=node
        self.children=children

    def dictify(self):
        d = {"name":"", "args":[child.dictify() for child in self.children]}
        match self.node:
            case Integer():
                d["name"] = "Int"
                d["args"] = [{"name":self.node.value}]
            case Bytes():
                d["name"] = "Bytes"
                d["args"] = [{"name":self.node.value}]
            case Comment():
                d = {"name":"Comment","args":[self.node.comment]}
            case TealVersion():
                d["name"] = "version"
                d["args"] = [self.node.version]
            case Exit():
                ge: GenericExpression = self.node.expression.expression
                d["name"] = "Exit"
                d["args"] = [traverse(ge.node).dictify()]
            case Switch():
                d["name"]="Cond"
            case SwitchOption():
                d["name"] = repr(self.node.expression.expression.node.value)
                print(self.node.__dict__)
                print(self.node.expression.__class__)
                print(self.node.expression.expression.__class__)
                print(self.node.expression.expression.node.__class__)
                print(self.node.expression.expression.node.__dict__)
            case IfThen():
                print("IN IFTHEN")
                print(self.node.__dict__)
                d["name"] = "Then"
            case IfStatement():
                d["name"] = "If"
                d["args"] = [traverse(self.node.condition.expression.node).dictify()]
                d["args"] += [traverse(node).dictify() for node in self.node.nodes]
                print(self.node.__dict__)
            case Block():
                d["name"] = "Seq"
            case BinaryOp():
                d["name"] = self.node.op
                a = traverse(self.node.a)
                b = traverse(self.node.b)
                d["args"] = [a.dictify(), b.dictify()]
            case TxnField():
                d["name"] = "Txn."+self.node.field
            case GlobalField():
                d["name"] = "Global."+self.node.field
            case Program():
                d["name"] = "program"
            case Blank():
                d["name"] = "blank"
            case _:
                print("NOMATCH", self.node.expression.node.__class__)

        print(d)
        if len(d["args"]) == 0:
            del d["args"]

        return d

def traverse(n: Node) -> TealishExpr:
    nodes: list[TealishExpr] = []
    if hasattr(n, 'nodes') and len(n.nodes)>0:
        for node in n.nodes:
            nodes.append(traverse(node))
    return TealishExpr(node=n, children=nodes)


def cli():
    path = Path(sys.argv[1])
    if path.is_dir():
        paths = path.glob("*.tl")
    else:
        paths = [path]

    import json
    for path in paths:
        compiler = build_tree(open(path).read())
        compiler.compile()
        tree = traverse(compiler.nodes[0])
        #tree.dictify()
        print(json.dumps(tree.dictify(), indent=2))


        #teal, min_teal, source_map = compile_program(open(path).read())
        #output_path = Path(path).parent / "build"
        #output_path.mkdir(exist_ok=True)
        #filename = Path(path).name
        #base_filename = filename.replace(".tl", "")
        #print(teal)

        ## Teal
        #with open(output_path / f"{base_filename}.teal", "w") as f:
        #    f.write("\n".join(teal))

        ## Min Teal
        #with open(output_path / f"{base_filename}.min.teal", "w") as f:
        #    f.write("\n".join(min_teal))

        ## Source Map
        #with open(output_path / f"{base_filename}.map.json", "w") as f:
        #    f.write(json.dumps(source_map).replace("],", "],\n"))
