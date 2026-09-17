"""Printer backends. No test here may ever reach real hardware."""

import pytest

from movingbox.labels import layout, printer


def a_label():
    return layout.render(
        layout.LabelData(code="B-0042", url="https://x.test/b/B-0042", room="Kitchen")
    )


def test_the_default_backend_is_fake(config):
    assert isinstance(printer.get_backend(config), printer.FakePrinter)


def test_an_unknown_backend_names_the_valid_ones(config):
    with pytest.raises(ValueError, match="brother_ql"):
        printer.get_backend(config.replace(printer_backend="dot_matrix"))


def test_the_fake_backend_writes_a_preview_instead_of_printing(config):
    backend = printer.get_backend(config)

    written = backend.print_label(a_label(), code="B-0042")

    assert written.exists()
    assert written.suffix == ".png"
    assert "B-0042" in written.name


def test_the_fake_backend_creates_its_preview_directory(config):
    # config points at a tmp dir that does not exist yet.
    assert not config.label_preview_dir.exists()

    printer.get_backend(config).print_label(a_label(), code="B-0001")

    assert config.label_preview_dir.is_dir()


class TestInstructions:
    """Raster generation is testable without a printer attached."""

    def test_a_correctly_sized_label_converts_to_raster(self):
        data = printer.build_instructions(a_label(), model="QL-800", label="62")

        assert len(data) > 0
        # ESC i a -- 'select mode', the first thing a QL-800 expects.
        assert data[:3] == b"\x1b\x69\x61"

    def test_a_wrongly_sized_label_is_rejected_with_a_clear_error(self):
        # brother_ql would otherwise try to rescale via PIL.Image.ANTIALIAS,
        # which Pillow removed in v10, and die with an unrelated AttributeError.
        narrow = a_label().resize((500, 400))

        with pytest.raises(ValueError, match="696"):
            printer.build_instructions(narrow, model="QL-800", label="62")


class TestCupsCommand:
    def test_the_queue_name_is_passed_to_lp_as_a_raw_job(self):
        command = printer._lp_command("Brother_QL_800")

        assert command[0] == "lp"
        assert "Brother_QL_800" in command
        assert "raw" in " ".join(command)

    def test_a_missing_queue_name_is_an_error_not_a_default_printer(self):
        # Falling back to the default printer would send a 40 KB raster to
        # whatever laser printer happens to be first in the list.
        with pytest.raises(ValueError):
            printer._lp_command(None)
