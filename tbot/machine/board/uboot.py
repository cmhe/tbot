# tbot, Embedded Automation Tool
# Copyright (C) 2018  Harald Seiler
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import typing
import shlex
import tbot
from tbot import machine
from tbot.machine import board
from tbot.machine import linux
from tbot.machine import channel
from . import special

B = typing.TypeVar("B", bound=board.Board)


class UBootMachine(board.BoardMachine[B], machine.InteractiveMachine):
    r"""
    Generic U-Boot board machine.

    **Example implementation**::

        from tbot.machine import board

        class MyBoard(board.Board):
            ...

        class MyBoardUBoot(board.UBootMachine[MyBoard]):
            prompt = "=> "

        BOARD = MyBoard
        UBOOT = MyBoardUBoot

    :ivar str bootlog:
        Messages that were printed out during startup.  You can access this
        attribute inside your testcases to get info about what was going on
        during boot.
    """

    autoboot_prompt: typing.Optional[str] = r"Hit any key to stop autoboot:\s+\d+\s+"
    """
    Regular expression of the autoboot prompt.
    Set this to ``None`` if autoboot is disabled for this board.
    """

    autoboot_keys = "\n"
    """
    Keys that should be sent to intercept autoboot
    """

    prompt = "U-Boot> "
    """
    U-Prompt that was configured when building U-Boot
    """

    @property
    def name(self) -> str:
        """Name of this U-Boot machine."""
        return self.board.name + "-uboot"

    def __init__(self, board: B) -> None:  # noqa: D107
        super().__init__(board)

        self.channel: channel.Channel
        if board.channel is not None:
            self.channel = board.channel
        else:
            raise RuntimeError("{board!r} does not support a serial connection!")

        with tbot.log.EventIO(
            ["board", "uboot", board.name],
            tbot.log.c("UBOOT").bold + f" ({self.name})",
            verbosity=tbot.log.Verbosity.QUIET,
        ) as boot_ev:
            boot_ev.verbosity = tbot.log.Verbosity.STDOUT
            boot_ev.prefix = "   <> "
            if self.autoboot_prompt is not None:
                boot_log = self.channel.read_until_prompt(
                    self.autoboot_prompt, regex=True, stream=boot_ev
                )

                boot_ev.data["output"] = boot_log
                self.channel.send(self.autoboot_keys)
                self.channel.read_until_prompt(self.prompt)
            else:
                self.channel.read_until_prompt(self.prompt, stream=boot_ev)

            self.bootlog = boot_ev.getvalue().split("\n", 1)[1]

    def destroy(self) -> None:
        """Destroy this U-Boot machine."""
        self.channel.close()

    def build_command(
        self, *args: typing.Union[str, special.Special, linux.Path[linux.LabHost]]
    ) -> str:
        """
        Return the string representation of a command.

        :param args: Each argument can either be a :class:`str` or a special token
            like :class:`~tbot.machine.board.Env`.
        :rtype: str
        """
        command = ""
        for arg in args:
            if isinstance(arg, linux.Path):
                arg = arg.relative_to("/tftpboot")._local_str()

            if isinstance(arg, special.Special):
                command += arg.resolve_string() + " "
            else:
                command += f"{shlex.quote(arg)} "

        return command[:-1]

    def exec(
        self, *args: typing.Union[str, special.Special, linux.Path[linux.LabHost]]
    ) -> typing.Tuple[int, str]:
        """
        Run a command in U-Boot and check its return value.

        :param args: Each argument can either be a :class:`str` or a special token
            like :class:`~tbot.machine.board.Env`.
        :rtype: tuple[int, str]
        :returns: A tuple with the return code and output of the command
        """
        command = self.build_command(*args)

        with tbot.log_event.command(self.name, command) as ev:
            ev.prefix = "   >> "
            ret, out = self.channel.raw_command_with_retval(
                command, prompt=self.prompt, stream=ev
            )
            ev.data["stdout"] = out

        return ret, out

    def exec0(
        self, *args: typing.Union[str, special.Special, linux.Path[linux.LabHost]]
    ) -> str:
        """
        Run a command in U-Boot and ensure its return value is zero.

        :param args: Each argument can either be a :class:`str` or a special token
            like :class:`~tbot.machine.board.Env`.
        :rtype: str
        :returns: The output of the command
        """
        ret, out = self.exec(*args)

        if ret != 0:
            raise tbot.machine.CommandFailedException(
                self, self.build_command(*args), out
            )

        return out

    def test(
        self, *args: typing.Union[str, special.Special, linux.Path[linux.LabHost]]
    ) -> bool:
        """
        Run a command and test if it succeeds.

        :param args: Each argument can either be a :class:`str` or a special token
            like :class:`~tbot.machine.board.Env`.
        :rtype: bool
        :returns: ``True`` if the return code is 0, else ``False``.
        """
        ret, _ = self.exec(*args)
        return ret == 0

    def env(self, var: str) -> str:
        """
        Get the value of an environment variable.

        :param str var: The variable's name
        :rtype: str
        :returns: Value of the environment variable
        """
        return self.exec0("echo", special.Raw(f"${{{var}}}"))[:-1]

    def interactive(self) -> None:
        """
        Drop into an interactive U-Boot session.

        :raises RuntimeError: If tbot was not able to reacquire the shell
            after the session is over.
        """
        tbot.log.message("Entering interactive shell (CTRL+D to exit) ...")

        self.channel.send(" \n")
        self.channel.attach_interactive()

        self.channel.send(" \n")
        try:
            self.channel.read_until_prompt(self.prompt, timeout=0.5)
        except TimeoutError:
            raise RuntimeError("Failed to reacquire U-Boot after interactive session!")

        tbot.log.message("Exiting interactive shell ...")
