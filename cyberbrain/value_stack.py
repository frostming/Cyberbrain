"""A self maintained value stack."""

import dis
import types

from typing import Optional

from .basis import Mutation


class ValueStackException(Exception):
    pass


# Sometimes we need to put a _placeholder on TOS because we don't care about its value,
# like LOAD_CONST. We convert it to [] when putting it on the stack.
_placeholder = None

# TODO: implement debug version that prints stack changes.


class ValueStack:
    """This class simulates the intepreter's value stack."""

    def __init__(self):
        self.stack = []

        # Stores extended args, insertion order = order of appearances. e.g.
        #   0 EXTENDED_ARG 1
        #   2 EXTENDED_ARG 2
        # extended_args is [1, 2].
        #
        # Handler *MUST* clear stored args once it's consumed !!!!!
        self.extended_args = []

    def emit_mutation(self, instr) -> Optional[Mutation]:
        """Given a instruction, emits mutation(s), if any."""

        # Binary operations are all the same, no need to define handlers individually.
        if instr.opname.startswith("BINARY_"):
            self._BINARY_operation_handler(instr)
            return

        # emits mutation and updates value stack.
        return getattr(self, f"_{instr.opname}_handler")(instr)

    @property
    def tos(self):
        try:
            return self.stack[-1]
        except IndexError:
            ValueStackException("Value stack should have tos but is empty.")

    @property
    def tos1(self):
        try:
            return self.stack[-2]
        except IndexError:
            ValueStackException("Value stack should have tos1 but does not.")

    def _push(self, value):
        """Pushes a value onto the simulated value stack.

        This method will automatically convert single value to a list. _placeholder will
        be converted to an empty list, so that it never exists on the value stack.
        """
        if value is _placeholder:
            value = []
        elif not isinstance(value, list):
            value = [value]
        self.stack.append(value)

    def _pop(self, n=1):
        """Pops and returns n item from stack."""
        try:
            if n == 1:
                return self.stack.pop()
            return [self.stack.pop() for _ in range(n)]
        except IndexError:
            ValueStackException("Value stack should have tos but is empty.")

    def _replace_tos(self, new_value):
        self._pop()
        self._push(new_value)

    def _pop_n_push_one(self, n):
        """Pops n elements from TOS, and pushes one to TOS.

        The pushed element is expected to originates from the popped elements.
        """
        elements = []
        for _ in range(n):
            elements.extend(self._pop())  # Flattens elements in TOS.
        self._push(elements)

    def _pop_one_push_n(self, n):
        """Pops one elements from TOS, and pushes n elements to TOS.

        The pushed elements are expected to originates from the popped element.
        """
        tos = self._pop()
        for _ in range(n):
            self._push(tos)

    def _POP_TOP_handler(self, instr):
        self._pop()

    def _ROT_TWO_handler(self, instr):
        tos, tos1 = self._pop(2)
        self._push(tos)
        self._push(tos1)

    def _DUP_TOP_handler(self, instr):
        self._push(self.tos)

    def _UNARY_POSITIVE_handler(self, instr):
        pass

    def _UNARY_NEGATIVE_handler(self, instr):
        pass

    def _UNARY_NOT_handler(self, instr):
        pass

    def _UNARY_INVERT_handler(self, instr):
        pass

    def _BINARY_operation_handler(self, instr):
        self._pop_n_push_one(2)

    def _RETURN_VALUE_handler(self, instr):
        self._pop()

    def _STORE_NAME_handler(self, instr):
        mutation = Mutation(target=instr.argval, sources=set(self.tos))
        self._pop()
        return mutation

    def _UNPACK_SEQUENCE_handler(self, instr):
        self._pop_one_push_n(instr.arg)

    def _UNPACK_EX_handler(self, instr):
        # sum(self.extended_args) is the number of values after *receiver,
        # instr.arg is number of values before *receiver, plus *receiver itself.
        assert len(self.extended_args) <= 1
        number_of_receivers = sum(self.extended_args) + 1 + instr.arg
        self._pop_one_push_n(number_of_receivers)
        self.extended_args.clear()

    def _STORE_ATTR_handler(self, instr):
        assert len(self.tos) == 1
        mutation = Mutation(target=self.tos[0], sources=set(self.tos1))
        return mutation

    def _LOAD_CONST_handler(self, instr):
        # For instructions like LOAD_CONST, we just need a placeholder on the stack.
        self._push(_placeholder)

    def _LOAD_NAME_handler(self, instr):
        # Note that we never store the actual rvalue, just name.
        self._push(instr.argrepr)

    def _BUILD_TUPLE_handler(self, instr):
        self._pop_n_push_one(instr.arg)

    def _BUILD_LIST_handler(self, instr):
        self._BUILD_TUPLE_handler(instr)

    def _BUILD_SET_handler(self, instr):
        self._BUILD_TUPLE_handler(instr)

    def _BUILD_MAP_handler(self, instr):
        self._pop_n_push_one(instr.arg * 2)

    def _BUILD_CONST_KEY_MAP_handler(self, instr):
        self._pop_n_push_one(instr.arg + 1)

    def _LOAD_ATTR_handler(self, instr):
        """Change the behavior of LOAD_ATTR.

        The effect of LOAD_ATTR is: Replaces TOS with getattr(TOS, co_names[namei]).
        However, this will make backtracing hard, because it eliminates the information
        about the source object, where the attribute originates from.

        Example: a = b.x
              0 LOAD_NAME                0 (b)
              2 LOAD_ATTR                1 (x)
              4 STORE_NAME               2 (a)
              6 LOAD_CONST               0 (None)
              8 RETURN_VALUE

        Tos was 'b' (in our customized version of LOAD_NAME), then LOAD_ATTR replaces it
        with the value of `b.x`. This caused 'a' to lost from the value stack, but we
        need it to know that b caused a to change.

        Example: a.x.y = 1
              0 LOAD_CONST               0 (1)
              2 LOAD_NAME                0 (a)
              4 LOAD_ATTR                1 (x)
              6 STORE_ATTR               2 (y)
              8 LOAD_CONST               1 (None)
             10 RETURN_VALUE

        Tos was 'a', then LOAD_ATTR replaces it with the value of `a.x`. This caused 'a'
        to lost from the value stack, but we need it to know that a's value has changed.

        In both examples, we can see what the value of the attribute doesn't mean much
        to us, so it's fine if we don't store it. But the "name" of the source object is
        vital, so we need to keep it. Thus, the handler just does nothing.
        """

    def _LOAD_FAST_handler(self, instr):
        self._push(instr.argrepr)

    def _STORE_FAST_handler(self, instr):
        return self._STORE_NAME_handler(instr)

    def _LOAD_METHOD_handler(self, instr):
        self._pop()

    def _CALL_METHOD_handler(self, instr):
        """
        For now we assume no argument is passed, so tos is replaced with return value,
        thus a noop.
        TODO: Implement full behaviors of CALL_METHOD.
        """
        pass

    def _EXTENDED_ARG_handler(self, instr):
        self.extended_args.append(instr.arg)