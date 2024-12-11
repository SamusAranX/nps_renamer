#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import csv
import glob
import hashlib
import os.path
import re
import shutil
import struct
import sys
from dataclasses import dataclass
from os import makedirs
from os.path import basename
from re import match

import unicodedata


@dataclass
class TSVInfo:
	console: str
	type: str

	def dir_path(self) -> str:
		return os.path.join(self.console, self.type)


@dataclass
class TSVEntry:
	title_id: str
	region: str
	name: str
	content_id: str
	app_version: str
	update_version: str
	sha256: str
	info: TSVInfo

	def file_name(self, ext: str) -> str:
		if self.update_version:
			return f"{self.name} ({self.update_version}) [{self.title_id}]{ext}"

		return f"{self.name} [{self.title_id}]{ext}"


info = {
	"PS3_AVATARS.tsv": TSVInfo("PS3", "avatar"),
	"PS3_DEMOS.tsv": TSVInfo("PS3", "demo"),
	"PS3_DLCS.tsv": TSVInfo("PS3", "dlc"),
	"PS3_GAMES.tsv": TSVInfo("PS3", "game"),
	"PS3_THEMES.tsv": TSVInfo("PS3", "theme"),
	"PSM_GAMES.tsv": TSVInfo("PSM", "game"),
	"PSP_DLCS.tsv": TSVInfo("PSP", "dlc"),
	"PSP_GAMES.tsv": TSVInfo("PSP", "game"),
	"PSP_THEMES.tsv": TSVInfo("PSP", "theme"),
	"PSP_UPDATES.tsv": TSVInfo("PSP", "update"),
	"PSV_DEMOS.tsv": TSVInfo("PSV", "demo"),
	"PSV_DLCS.tsv": TSVInfo("PSV", "dlc"),
	"PSV_GAMES.tsv": TSVInfo("PSV", "game"),
	"PSV_THEMES.tsv": TSVInfo("PSV", "theme"),
	"PSV_UPDATES.tsv": TSVInfo("PSV", "update"),
	"PSX_GAMES.tsv": TSVInfo("PSX", "game"),
}
pkg_re = re.compile(r"^([A-Z]{2}\d{4}-([A-Z]{4}\d{5})_00-.*?)(?:_patch_(.*?))?\.pkg$", re.I)


def sanitize_file_name(value: str) -> str:
	value = unicodedata.normalize("NFC", value)
	return re.sub(r"[<>:\"/\\|?*]", "_", value).strip()


def is_pkg(filename: str) -> bool:
	with open(filename, "rb") as f:
		return struct.unpack("<i", f.read(4))[0] == 0x474B507F


def sha256sum_new(filename: str) -> str:
	with open(filename, "rb", buffering=0) as f:
		return hashlib.file_digest(f, "sha256").hexdigest()


# sha256sum() but for python versions < 3.11
def sha256sum_old(filename: str) -> str:
	h = hashlib.sha256()
	b = bytearray(128 * 1024)
	mv = memoryview(b)
	with open(filename, "rb", buffering=0) as f:
		while n := f.readinto(mv):
			h.update(mv[:n])
	return h.hexdigest()


def sha256sum(filename: str) -> str:
	try:
		# try the python 3.11+ method first
		return sha256sum_new(filename)
	except AttributeError:
		# fall back to the python < 3.11 method
		return sha256sum_old(filename)


def predicate_filename(entry: TSVEntry, content_id: str, title_id: str, patch: str) -> bool:
	patch = patch.lstrip("0")
	if patch:
		return entry.title_id == title_id and entry.update_version == patch

	return entry.content_id == content_id and entry.title_id == title_id


def predicate_hash(entry: TSVEntry, sha256: str) -> bool:
	return entry.sha256 == sha256


def row_val(headers: list[str], row: list[str], col_name: str) -> str:
		try:
			return row[headers.index(col_name)]
		except ValueError:
			return ""


def main(args):
	tsv_files = glob.glob(os.path.join(args.tsv_dir, "*.tsv"))
	tsv_files.sort(reverse=True)

	# read TSV files into memory
	entries: list[TSVEntry] = []
	print("Loading TSV files…")
	for tsv_file in tsv_files:
		tsv_base = basename(tsv_file)
		tsv_info = info[tsv_base]

		with open(tsv_file, "r", encoding="utf8") as f:
			rd = csv.reader(f, dialect="excel-tab")
			for idx, row in enumerate(rd):
				if idx == 0:
					headers = row

				entry = TSVEntry(
					row_val(headers, row, "Title ID"),
					row_val(headers, row, "Region"),
					row_val(headers, row, "Name"),
					row_val(headers, row, "Content ID"),
					row_val(headers, row, "App Version"),
					row_val(headers, row, "Update Version"),
					row_val(headers, row, "SHA256"),
					tsv_info,
				)
				entries.append(entry)

	if not entries:
		print("The TSV files are not optional")
		sys.exit(1)

	print("TSV files loaded")

	print("Finding .pkg files…")
	pkg_files = glob.glob(os.path.join(args.pkg_dir, "**", "*.pkg"), recursive=True)
	if not pkg_files:
		print("No .pkg files found")
		return

	print(f"{len(pkg_files)} .pkg", "file" if len(pkg_files) == 1 else "files", "found")

	# dict of path: number of times encountered
	dupe_paths: dict[str, int] = {}

	# list of (src_path, dest_path)
	move_files: list[tuple[str, str]] = []

	# list of files that weren't found in the TSV files
	unhandled_files: list[str] = []

	# find .pkg files in the data from the TSV files and assemble a list of src -> dest paths
	for pkg_file in pkg_files:
		if not is_pkg(pkg_file):
			continue

		if matches := pkg_re.findall(basename(pkg_file)):
			content_id, title_id, patch = matches[0]
			matching_entry = next((e for e in entries if predicate_filename(e, content_id, title_id, patch)), None)
		else:
			print(f"Trying SHA256 for {pkg_file}…")
			sha256 = sha256sum(pkg_file)
			matching_entry = next((e for e in entries if predicate_hash(e, sha256)), None)

		if not matching_entry:
			unhandled_files.append(pkg_file)
			continue

		if args.copy_dir:
			dest_dir = os.path.join(args.copy_dir, matching_entry.info.dir_path())
		else:
			dest_dir = os.path.join(args.pkg_dir, matching_entry.info.dir_path())

		pkg_ext = os.path.splitext(pkg_file)[1]
		dest_file = matching_entry.file_name(pkg_ext)
		dest_file = sanitize_file_name(dest_file)
		dest_path = os.path.join(dest_dir, dest_file)

		# check whether the generated file path has already been encountered, if so, increment a counter and append it to the file
		dest_path_lower = dest_path.lower()
		if dest_path_lower in dupe_paths:
			dupe_paths[dest_path_lower] += 1
			dest_file_root, dest_file_ext = os.path.splitext(dest_file)
			dest_path_new = os.path.join(dest_dir, f"{dest_file_root} ({dupe_paths[dest_path_lower]}){dest_file_ext}")
			print(f"Encountered duplicate destination file path {dest_path}, renamed to {basename(dest_path_new)}")
			dest_path = dest_path_new
		else:
			dupe_paths[dest_path_lower] = 0

		makedirs(dest_dir, exist_ok=True)

		if pkg_file != dest_path:
			move_files.append((pkg_file, dest_path))

	if not move_files:
		print("There's nothing to do!")
		return

	for src_path, dest_path in move_files:
		try:
			if args.copy_dir:
				print("Copying", src_path, "to", dest_path)
				if not args.dry_run:
					shutil.copyfile(src_path, dest_path)
			else:
				print("Moving", src_path, "to", dest_path)
				if not args.dry_run:
					shutil.move(src_path, dest_path)
		except shutil.Error as e:
			print("Unable to", "copy" if args.copy_dir else "move", src_path, "to", dest_path)
			raise e

	for pkg_file in unhandled_files:
		print("Couldn't", "copy" if args.copy_dir else "move", pkg_file, "because it was not found in the TSV files")


if __name__ == "__main__":
	parser = argparse.ArgumentParser(description="PKG autorenamer")
	parser.add_argument("-t", "--tsv-dir", metavar="TSV DIR", type=str, required=True, help="The directory containing the required .tsv files")
	parser.add_argument("-c", "--copy-dir", metavar="COPY DESTINATION", type=str, help="Specify a directory here to copy the renamed files there instead of renaming in place (Potentially slower)")
	parser.add_argument("-n", "--dry-run", action="store_true", help="Don't perform any move or copy operations")
	parser.add_argument("pkg_dir", type=str, help="The pkg directory")

	main(parser.parse_args())
