# class Tree(object):
#     def __init__(self, data):
#         self.data = data
#         self.children = []

#     def add_child(self, obj):
#         self.children.append(obj)

# a = Tree("a")
# b= Tree("b")
# c = Tree("c")

# a.add_child(b)
# a.add_child(c)


# def paths(tree: Tree, list_of_paths: list[str]) -> None:
#     """return all paths"""
#     list_of_paths.append(tree.data)

#     if tree.children == []:
#         for node in list_of_paths:
#             print(node)
#         print("/n")
#         return None

#     for child in tree.children:
#         paths(child,list_of_paths)

# def paths2(tree: Tree, list_of_paths: list[str]) -> list[str]:
#     """return all paths"""
#     list_of_paths.append(tree.data)

#     if tree.children == []:
#         return list_of_paths

#     big_return_list = []
#     for child in tree.children:
#         new_list = list_of_paths
#         new_list.append(child.data)


# if __name__ == "__main__":
#     a = Tree("a")
#     b= Tree("b")
#     c = Tree("c")

#     mylist: list=[]
#     a.add_child(b)
#     a.add_child(c)
#     paths(a,mylist)
class Tree:
    """general tree"""

    def __init__(self, data, children=None):
        if children is None:
            children = []
        self.data = data
        self.children = children

    def __str__(self):
        return str(self.data)

    __repr__ = __str__


def get_all_paths(tree: Tree, path=None):
    """returns all paths"""
    paths = []
    if path is None:
        path = []
    path.append(tree)
    if tree.children:
        for child in tree.children:
            paths.extend(get_all_paths(child, path[:]))
    else:
        paths.append(path)
    return paths


mytree = Tree("a", [Tree("b"), Tree("c", [Tree("d"), Tree("e")])])

if __name__ == "__main__":
    mytree = Tree("a", [Tree("b"), Tree("c", [Tree("d"), Tree("e")])])

    paths = get_all_paths(mytree)
    print(paths)
