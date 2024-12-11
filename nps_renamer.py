#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import csv
import glob
import os.path
import re
import shutil
import sys
from dataclasses import dataclass
from os import makedirs
from os.path import basename

import unicodedata


@dataclass
class TSVInfo:
	console: str
	type: str

	def path(self) -> str:
		return os.path.join(self.console, self.type)


@dataclass
class TSVEntry:
	title_id: str
	region: str
	name: str
	content_id: str
	app_version: str
	update_version: str
	info: TSVInfo


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
pkg_re = re.compile(r"^(.*?-([A-Z]{4}\d{5})_00-.*?)(?:_patch_(.*?))?\.pkg$", re.I)


def sanitize_file_name(value: str) -> str:
	value = unicodedata.normalize("NFC", value)
	return re.sub(r"[<>:\"/\\|?*]", "_", value).strip()


def main(args):
	tsv_files = glob.glob(os.path.join(args.tsv_dir, "*.tsv"))
	pkg_files = glob.glob(os.path.join(args.pkg_dir, "*.pkg"))

	def row_val(headers: list[str], row: list[str], col_name: str) -> str:
		try:
			return row[headers.index(col_name)]
		except ValueError:
			return ""

	# read TSV files into memory
	entries: list[TSVEntry] = []
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
					tsv_info,
				)
				entries.append(entry)

	if not entries:
		print("The TSV files are not optional")
		sys.exit(1)

	# matching function
	def predicate(entry: TSVEntry, content_id: str, title_id: str, patch: str) -> bool:
		patch = patch.lstrip("0")
		if patch:
			return entry.title_id == title_id and entry.update_version == patch

		return entry.content_id == content_id and entry.title_id == title_id

	# dict of path: number of times encountered
	dupe_paths: dict[str, int] = {}

	# list of (src_path, dest_path)
	move_files: list[tuple[str, str]] = []

	# list of files that weren't found in the TSV files
	unhandled_files: list[str] = []

	for pkg_file in pkg_files:
		pkg_base = basename(pkg_file)
		pkg_ext = os.path.splitext(pkg_base)[1]
		content_id, title_id, patch = pkg_re.findall(pkg_base)[0]
		matching_entry = next((e for e in entries if predicate(e, content_id, title_id, patch)), None)
		if not matching_entry:
			unhandled_files.append(pkg_file)
			continue

		if args.copy_dir:
			dest_dir = os.path.join(args.copy_dir, matching_entry.info.path())
		else:
			dest_dir = os.path.join(args.pkg_dir, matching_entry.info.path())
		dest_file = f"[{matching_entry.title_id}] {matching_entry.name}{pkg_ext}"
		if matching_entry.update_version:
			dest_file = f"[{matching_entry.title_id}] {matching_entry.name} ({matching_entry.update_version}){pkg_ext}"

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
		move_files.append((pkg_file, dest_path))

	for src_path, dest_path in move_files:
		try:
			if args.copy_dir:
				print("Copying", src_path, "to", dest_path)
				if not args.dry_run:
					shutil.copy(src_path, dest_path)
			else:
				print("Moving", src_path, "to", dest_path)
				if not args.dry_run:
					shutil.move(src_path, dest_path)
		except (OSError, FileNotFoundError, shutil.Error) as e:
			print("Unable to", "copy" if args.copy_dir else "move", src_path, "to", dest_path)
			raise e

	for pkg_file in unhandled_files:
		print(f"Couldn't handle {pkg_file} because it was not found in the TSV files")


if __name__ == "__main__":
	parser = argparse.ArgumentParser(description="PKG autorenamer")
	parser.add_argument("-t", "--tsv-dir", metavar="tsv dir", type=str, required=True, help="The directory containing the required .tsv files")
	parser.add_argument("-c", "--copy-dir", metavar="copy destination", type=str, help="Specify a directory here to copy the renamed files there instead of renaming in place (Potentially slower)")
	parser.add_argument("-n", "--dry-run", action="store_true", help="Don't perform any move or copy operations")
	parser.add_argument("pkg_dir", type=str, help="The pkg directory")

	main(parser.parse_args())
