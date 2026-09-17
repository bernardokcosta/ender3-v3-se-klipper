# Pure helpers for the Ender 3 V3 SE display
#
# Copyright (C) 2026 Bernardo Costa
#
# This file may be distributed under the terms of the GNU GPLv3 license.

def validate_filename(value):
    if any(char in value for char in ("\x00", "\r", "\n")):
        raise ValueError("Filenames may not contain NUL or line breaks")
    return value


class FileEntry:
    def __init__(self, name, path, is_dir):
        self.name = name
        self.path = path
        self.is_dir = is_dir


class FileBrowser:
    def __init__(self):
        self.path = ""
        self.entries = []

    def reset(self):
        self.path = ""
        self.entries = []

    def update(self, files):
        prefix = self.path + "/" if self.path else ""
        directories = {}
        regular_files = []
        for file_path, unused_size in files:
            normalized = file_path.lstrip("/")
            if not normalized.startswith(prefix):
                continue
            remainder = normalized[len(prefix):]
            if not remainder:
                continue
            parts = remainder.split("/")
            if len(parts) > 1:
                name = parts[0]
                directories[name] = prefix + name
            else:
                regular_files.append(FileEntry(parts[0], normalized, False))
        dir_entries = [FileEntry(name, path, True)
                       for name, path in directories.items()]
        dir_entries.sort(key=lambda entry: entry.name.lower())
        regular_files.sort(key=lambda entry: entry.name.lower())
        self.entries = dir_entries + regular_files
        return list(self.entries)

    def enter(self, entry):
        if entry.is_dir:
            self.path = entry.path
            return None
        return entry.path

    def back(self):
        if not self.path:
            return False
        self.path = self.path.rsplit("/", 1)[0] if "/" in self.path else ""
        return True
