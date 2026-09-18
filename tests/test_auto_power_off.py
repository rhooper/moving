"""Disabling the printer's auto power-off, and serialising access to it.

The command and its framing come from i3labelstation's brother_ql.cpp, which
drives the same QL-800 in production:

    400 x 0x00            invalidate (QL-800; older models use 200)
    1B 40                 ESC @        initialise
    1B 69 55 41 00 00     ESC i U A    auto power-off, timeout 0 = disabled

It is written to the printer's own memory and persists, which is why the
watcher can send it once and stop rather than poll forever.
"""

import threading
import time

import pytest

from movingbox.labels import printer


class TestTheCommand:
    def test_it_ends_with_the_auto_power_off_subcommand(self):
        assert printer.auto_power_off_command().endswith(b"\x1b\x69\x55\x41\x00\x00")

    def test_a_zero_timeout_is_what_disables_it(self):
        # The last two bytes are the timeout in minutes; zero means never.
        assert printer.auto_power_off_command()[-2:] == b"\x00\x00"

    def test_it_is_preceded_by_an_initialise(self):
        command = printer.auto_power_off_command()

        assert command[-8:-6] == b"\x1b\x40"

    def test_it_opens_with_the_invalidate_the_ql800_expects(self):
        command = printer.auto_power_off_command()

        assert command[: printer.QL800_INVALIDATE] == bytes(printer.QL800_INVALIDATE)
        assert printer.QL800_INVALIDATE == 400

    def test_the_whole_thing_is_the_expected_length(self):
        # 400 invalidate + 2 init + 6 command. Pinned because a wrong length
        # here is a command the printer silently ignores.
        assert len(printer.auto_power_off_command()) == 408

    def test_the_invalidate_length_can_be_given_for_another_model(self):
        assert len(printer.auto_power_off_command(invalidate=200)) == 208


class TestSending:
    def test_no_printer_means_no_claim_of_success(self, config):
        assert printer.disable_auto_power_off(config, find_device=lambda: None) is False

    def test_a_usb_failure_is_reported_rather_than_raised(self, config):
        def explode():
            raise OSError("device busy")

        assert printer.disable_auto_power_off(config, find_device=explode) is False

    def test_the_bytes_reach_the_device(self, config):
        written = []

        class FakeDevice:
            def write(self, payload):
                written.append(bytes(payload))

            def close(self):
                pass

        assert printer.disable_auto_power_off(config, find_device=FakeDevice) is True
        assert written[0].endswith(b"\x1b\x69\x55\x41\x00\x00")


class TestReleasingTheDevice:
    """The watcher lives in the long-running service; a handle it keeps is a
    handle nothing else can have. This shipped broken once: the service held
    the QL-800 exclusively from startup (ioreg: UsbExclusiveOwner = the
    service's own pid), so brother_ql's second open -- every print job -- was
    refused, and the process was locked out of the printer by itself."""

    def test_the_device_is_released_once_the_command_is_sent(self, config):
        closed = []

        class FakeDevice:
            def write(self, payload):
                pass

            def close(self):
                closed.append(1)

        assert printer.disable_auto_power_off(config, find_device=FakeDevice) is True
        assert closed == [1]

    def test_it_is_released_even_when_the_write_fails(self, config):
        closed = []

        class FakeDevice:
            def write(self, payload):
                raise OSError("pipe error")

            def close(self):
                closed.append(1)

        assert printer.disable_auto_power_off(config, find_device=FakeDevice) is False
        assert closed == [1]


class TestExclusiveAccess:
    """One device, and FastAPI runs sync endpoints in a threadpool."""

    def test_two_threads_never_hold_it_at_once(self):
        inside = []
        overlapped = threading.Event()

        def use():
            with printer.exclusive():
                inside.append(1)
                if len(inside) > 1:
                    overlapped.set()
                time.sleep(0.05)
                inside.pop()

        threads = [threading.Thread(target=use) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert not overlapped.is_set(), "two threads wrote to the printer at once"

    def test_it_is_released_even_when_the_job_fails(self):
        # A print that raises must not wedge the printer for every later job.
        with pytest.raises(RuntimeError):
            with printer.exclusive():
                raise RuntimeError("printer on fire")

        with printer.exclusive():
            pass  # would block forever if the lock had leaked


class TestTheWatcher:
    @pytest.fixture
    def real(self, config):
        """Config naming a real backend; the watcher skips `fake` by design."""
        return config.replace(printer_backend="brother_ql")

    def test_it_stops_once_the_command_lands(self, real):
        attempts = []

        def send(_config):
            attempts.append(1)
            return True

        watcher = printer.AutoOffWatcher(real, send=send, interval=0.01)
        watcher.start()
        watcher.join(timeout=2)

        assert not watcher.is_alive()
        # Persisted on the printer, so once is the whole job.
        assert len(attempts) == 1

    def test_it_keeps_looking_while_the_printer_is_absent(self, real):
        attempts = []

        def send(_config):
            attempts.append(1)
            return len(attempts) >= 3  # plugged in on the third look

        watcher = printer.AutoOffWatcher(real, send=send, interval=0.01)
        watcher.start()
        watcher.join(timeout=2)

        assert not watcher.is_alive()
        assert len(attempts) == 3

    def test_it_gives_up_rather_than_polling_forever(self, real):
        watcher = printer.AutoOffWatcher(
            real, send=lambda _c: False, interval=0.01, attempts=3
        )
        watcher.start()
        watcher.join(timeout=2)

        assert not watcher.is_alive()

    def test_it_can_be_stopped_early(self, real):
        # The service has to be able to shut down without waiting for it.
        watcher = printer.AutoOffWatcher(
            real, send=lambda _c: False, interval=5, attempts=100
        )
        watcher.start()
        watcher.stop()
        watcher.join(timeout=2)

        assert not watcher.is_alive()

    def test_it_does_nothing_when_the_backend_is_not_a_real_printer(self, config):
        # `fake` writes PNGs; there is no device to talk to. This one takes the
        # plain config on purpose -- the fake backend is the whole point.
        attempts = []
        watcher = printer.AutoOffWatcher(
            config, send=lambda _c: attempts.append(1) or True, interval=0.01
        )
        watcher.start()
        watcher.join(timeout=2)

        assert attempts == []
