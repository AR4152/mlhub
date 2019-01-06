#!/usr/bin/python3
#
# mlhub - Machine Learning Model Repository
#
# A command line tool for managing machine learning models.
#
# Copyright 2018 (c) Graham.Williams@togaware.com All rights reserved. 
#
# This file is part of mlhub.
#
# MIT License
#
# Permission is hereby granted, free of charge, to any person obtaining a copy 
# of this software and associated documentation files (the ""Software""), to deal 
# in the Software without restriction, including without limitation the rights 
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell 
# copies of the Software, and to permit persons to whom the Software is 
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in 
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED *AS IS*, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR 
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, 
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE 
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER 
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, 
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN 
# THE SOFTWARE.

import base64
import cgi
import distro
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import uuid
import yaml
import yamlordereddictloader
import zipfile

from fuzzywuzzy import fuzz
from fuzzywuzzy import process as fuzzprocess
from mlhub.constants import (
    APP,
    APPX,
    ARCHIVE_DIR,
    CACHE_DIR,
    CMD,
    COMMANDS,
    COMPLETION_COMMANDS,
    COMPLETION_DIR,
    COMPLETION_MODELS,
    DESC_YAML,
    DESC_YML,
    EXT_AIPK,
    EXT_MLM,
    LOG_DIR,
    META_YAML,
    META_YML,
    MLHUB,
    MLHUB_YAML,
    MLINIT,
    USAGE,
    VERSION,
)

# ----------------------------------------------------------------------
# MLHUB repo and model package
# ----------------------------------------------------------------------


def get_repo(mlhub):
    """Determine the repository to use: command line, environment, default."""

    repo = MLHUB
    if mlhub is not None:
        repo = os.path.join(repo, "")  # Ensure trailing slash.

    logger = logging.getLogger(__name__)
    logger.debug("repo: {}".format(repo))

    return repo


def get_repo_meta_data(repo):
    """Read the repositories meta data file and return as a list."""

    repo = get_repo(repo)

    try:
        url = repo + META_YAML
        meta_list = list(yaml.load_all(urllib.request.urlopen(url).read()))
    except urllib.error.URLError:
        try:
            url = repo + META_YML
            meta_list = list(yaml.load_all(urllib.request.urlopen(url).read()))
        except urllib.error.URLError:
            logger = logging.getLogger(__name__)
            logger.error('Repo connection problem.', exc_info=True)
            raise RepoAccessException(repo)

    return meta_list, repo


def print_meta_line(entry):
    """Print one line summary of a model."""

    meta = entry["meta"]
    name = meta["name"]
    version = meta["version"]
    try:
        title = meta["title"]
    except KeyError:
        title = meta["description"]

    # One line message.

    max_title = 24
    max_descr = 44

    long = ""
    if len(title) > max_descr:
        long = "..."

    formatter = "{0:<TITLE.TITLE} {1:^6} {2:<DESCR.DESCR}{3}".\
        replace("TITLE", str(max_title)).replace("DESCR", str(max_descr))
    print(formatter.format(name, version, title, long))


def get_version(model=None):
    if model is None:
        return VERSION
    else:
        entry = load_description(model)
        return entry["meta"]["version"]


def check_model_installed(model):
    """Check if model installed."""

    path = get_package_dir(model)

    logger = logging.getLogger(__name__)
    logger.debug("Check if package {} is installed at: {}".format(model, path))

    if not os.path.exists(path):
        raise ModelNotInstalledException(model)

    return True


def load_description(model):
    """Load description of the <model>."""

    desc = get_available_pkgyaml(model)
    entry = read_mlhubyaml(desc)

    return entry


def read_mlhubyaml(name):
    """Read description from a specified local yaml file or the url of a yaml file."""

    try:

        if is_github_url(name) and name.startswith("https://api"):
            res = json.loads(urllib.request.urlopen(name).read())
            content = base64.b64decode(res["content"])
        elif is_url(name):
            content = urllib.request.urlopen(name).read()
        else:
            content = open(name)

        # Use yamlordereddictloader to keep the order of entries specified inside YAML file.
        # Because the order of commands matters.

        entry = yaml.load(content, Loader=yamlordereddictloader.Loader)

    except (yaml.composer.ComposerError, yaml.scanner.ScannerError):

        raise MalformedYAMLException(name)

    except urllib.error.URLError:

        raise YAMLFileAccessException(name)

    return entry


def get_model_info_from_repo(model, repo):
    """Get model url on mlhub.

    Args:
        model (str): model name.
        repo (str): packages list url.

    Returns:
        url: model url for download.
        meta: list of all model meta data.

    Raises:
        ModelNotFoundOnRepoException
    """

    url = None
    version = None
    meta_list, repo = get_repo_meta_data(repo)

    # Find the first matching entry in the meta data.

    try:
        for entry in meta_list:
            meta = entry["meta"]
            if model == meta["name"]:
                if "yaml" in meta:
                    url = meta["yaml"]
                else:
                    url = meta["url"]

                # If url refers to an archive, its version must be known.

                if is_archive(url):
                    version = meta["version"]

                break
    except KeyError as e:
        raise MalformedPackagesDotYAMLException(e.args[0], model)

    # If not found suggest how a model might be installed.

    if url is None:
        logger = logging.getLogger(__name__)
        logger.error("Model '{}' not found on Repo '{}'.".format(model, repo))
        raise ModelNotFoundOnRepoException(model, repo)

    return url, version, meta_list


def interpret_mlm_name(mlm):
    """Interpret model package file name into model name and version number.

    Args:
        mlm (str): mlm file path or url.

    Returns:
        file name, model name, version number.
    """

    if not ends_with_mlm(mlm):
        raise MalformedMLMFileNameException(mlm)

    mlmfile = os.path.basename(mlm)
    try:
        model, version = mlmfile.split('_')
    except ValueError:
        raise MalformedMLMFileNameException(mlm)

    version = '.'.join(version.split('.')[: -1])

    return model, version


def get_available_pkgyaml(url):
    """Return the available package yaml file path.

    Possible options are MLHUB.yaml, DESCRIPTION.yaml or DESCRIPTION.yml.
    If both exist, MLHUB.yaml takes precedence.
    Path can be a path to the package directory or a URL to the top level of the pacakge repo
    """

    yaml_list = [MLHUB_YAML, DESC_YAML, DESC_YML]

    if is_github_url(url):
        yaml_list = [url.format(x) for x in yaml_list]
    elif is_url(url):
        yaml_list = ['/'.join([url, x]) for x in yaml_list]
    else:
        if os.path.sep not in url:  # url is a model name
            url = os.path.join(get_init_dir(), url)
        yaml_list = [os.path.join(url, x) for x in yaml_list]

    logger = logging.getLogger(__name__)
    logger.info("Finding MLHUB.yaml ...")
    logger.debug("Possible locations: {}".format(yaml_list))

    if is_url(url):
        param = yaml_list[0]
        for x in yaml_list:
            try:
                if urllib.request.urlopen(x).status == 200:
                    logger.debug("YAML: {}".format(x))
                    return x
            except urllib.error.URLError:
                continue
    else:
        param = url
        for x in yaml_list:
            if os.path.exists(x):
                logger.debug("YAML: {}".format(x))
                return x

    raise DescriptionYAMLNotFoundException(param)

# ----------------------------------------------------------------------
# String manipulation
# ----------------------------------------------------------------------


def dropdot(sentence):
    """Drop the period after a sentence."""
    return re.sub("[.]$", "", sentence)


def drop_newline(paragraph):
    """Drop trailing newlines."""

    return re.sub("\n$", "", paragraph)


def lower_first_letter(sentence):
    """Lowercase the first letter of a sentence."""

    return sentence[:1].lower() + sentence[1:] if sentence else ''


# ----------------------------------------------------------------------
# URL and download
# ----------------------------------------------------------------------


def is_url(name):
    """Check if name is a url."""

    return re.findall('http[s]?:', name)


def get_url_filename(url):
    """Obtain the file name from URL or None if not available."""

    info = urllib.request.urlopen(url).getheader('Content-Disposition')
    if info is None:  # File name can be obtained from URL per se.
        filename = os.path.basename(url)
        if filename == '':
            filename = None
    else:  # File name may be obtained from 'Content-Disposition'.
        _, params = cgi.parse_header(info)
        if 'filename' in params:
            filename = params['filename']
        else:
            filename = None

    return filename


def download_model_pkg(url, local, pkgfile, quiet):
    """Download the model package mlm or zip file from <url> to <local>."""

    if not quiet:
        print("Package " + url + "\n")

    meta = urllib.request.urlopen(url)
    if meta.status != 200:
        raise ModelURLAccessException(url)

    # Content-Length is not always necessarily available.

    dsize = meta.getheader("Content-Length")
    if dsize is not None:
        dsize = "{:,}".format(int(dsize))

    if not quiet:
        msg = "Downloading '{}'".format(pkgfile)
        if dsize is not None:
            msg += " ({} bytes)".format(dsize)
        msg += " ...\n"
        print(msg)

    # Download the archive from the URL.

    try:
        urllib.request.urlretrieve(url, local)
    except urllib.error.URLError as error:
        raise ModelDownloadHaltException(url, error.reason.lower())

# ----------------------------------------------------------------------
# Folder and file manipulation
# ----------------------------------------------------------------------


def _create_dir(path, error_msg, exception):
    """Create dir <path> if not exists.

    Args:
        path (str): the dir path.
        error_msg (str): log error message if mkdir fails.
        exception (Exception): The exception raised when error.
    """

    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        logger = logging.getLogger(__name__)
        logger.error(error_msg, exc_info=True)
        raise exception

    return path


def unpack_with_promote(file, dest, valid_name=None, remove_dst=True):
    """Unzip <file> into the directory <dest>.

    If all files in the zip file are under a top level directory,
    remove the top level dir and promote the dir level of those files.

    If <remove_dst> is True, then the directory <dest> will be remove first,
    otherwise, unextracted files will co-exist with those already in <dest>.

    Return whether promotion happend and the top level dir if did.
    """

    logger = logging.getLogger(__name__)

    # Check if need to remove <dest>.

    if remove_dst:
        remove_file_or_dir(dest)

    # Figure out if <file> is a Zipball or Tarball.

    if valid_name is None:
        valid_name = file

    if is_mlm_zip(valid_name):
        opener, lister_name, appender_name = zipfile.ZipFile, 'namelist', 'write'
    else:
        opener, lister_name, appender_name = tarfile.open, 'getnames', 'add'

    # Unpack <file>.

    with opener(file) as pkg_file:

        # Check if all files are under a top dir.

        file_list = getattr(pkg_file, lister_name)()
        first_segs = [x.split(os.path.sep)[0] for x in file_list]
        if (len(file_list) == 1 and os.path.sep in file_list[0]) or \
                (len(file_list) != 1 and all([x == first_segs[0] for x in first_segs])):
            promote, top_dir = True, file_list[0].split(os.path.sep)[0]
        else:
            promote, top_dir = False, None

        if not promote:  # All files are at the top level.

            logger.debug("Extract {} directly into {}".format(file, dest))
            pkg_file.extractall(dest)
            return False, top_dir, file_list

        else:  # All files are under a top dir.
            logger.debug("Extract {} without top dir into {}".format(file, dest))
            file_list = []
            with tempfile.TemporaryDirectory() as tmpdir:

                # Extract file.

                pkg_file.extractall(tmpdir)

                with tempfile.TemporaryDirectory() as tmpdir2:

                    # Repack files without top dir and then extract again into <dest>.
                    #
                    # Extraction can be done on a existing dir, without removing the dir first,
                    # and the extracted files can co-exist with the files already inside the dir,
                    # without affecting the existing files except they have the same name.

                    with opener(os.path.join(tmpdir2, 'tmpball'), 'w') as new_pkg_file:
                        appender = getattr(new_pkg_file, appender_name)
                        dir_path = os.path.join(tmpdir, top_dir)
                        for path, dirs, files in os.walk(dir_path):
                            for file in files:
                                file_path = os.path.join(path, file)
                                arc_path = os.path.relpath(file_path, dir_path)
                                file_list.append(arc_path)
                                appender(file_path, arc_path)

                    with opener(os.path.join(tmpdir2, 'tmpball')) as new_pkg_file:
                        new_pkg_file.extractall(dest)

            return True, top_dir, file_list


def remove_file_or_dir(path):
    """Remove an existing file or directory."""

    if os.path.exists(path):
        if os.path.isfile(path):
            os.remove(path)
        else:
            shutil.rmtree(path)


def make_symlink(src, dst):
    """Make a symbolic link from src to dst."""

    os.makedirs(os.path.dirname(dst), exist_ok=True)
    remove_file_or_dir(dst)
    os.symlink(src, dst)


def merge_folder(src_dir, dst_dir):
    """Move files from src_dir into dst_dir without removing existing files under dst_dir."""

    for path, dirs, files in os.walk(src_dir):
        for file in files:
            src = os.path.join(path, file)
            dst = os.path.join(dst_dir, os.path.relpath(src, src_dir))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(src, dst)


def dir_size(dirpath):
    """Get total size of dirpath."""

    return sum([sum(map(lambda f: os.path.getsize(os.path.join(pth, f)), files))
                for pth, dirs, files in os.walk(dirpath)])


def ends_with_mlm(name):
    """Check if name ends with .mlm or .aipk"""

    return name.endswith(EXT_MLM) or name.endswith(EXT_AIPK)


def is_mlm_zip(name):
    """Check if name is a MLM or Zip file."""

    return ends_with_mlm(name) or name.endswith(".zip")


def is_tar(name):
    """Check if name is a Tarball."""

    return name.endswith(".tar") or name.endswith(".gz") or name.endswith(".bz2")


def is_archive(name):
    """Check if name is a archive file."""

    return is_mlm_zip(name) or is_tar(name)


def is_description_file(name):
    """Check if name ends with DESCRIPTION.yaml or DESCRIPTION.yml"""

    return name.endswith(DESC_YAML) or name.endswith(DESC_YML) or name.endswith(MLHUB_YAML)

# ----------------------------------------------------------------------
# Help message
# ----------------------------------------------------------------------


def print_usage():
    print(CMD)
    print(USAGE.format(CMD, MLHUB, get_init_dir(), VERSION, APP))


def print_model_cmd_help(info, cmd):
    print("\n  $ {} {} {}".format(CMD, cmd, info["meta"]["name"]))

    c_meta = info['commands'][cmd]
    if type(c_meta) is str:
        print("    " + c_meta)
    else:
        # Handle malformed DESCRIPTION.yaml like
        # --
        # commands:
        #   print:
        #     description: print a textual summary of the model
        #   score:
        #     equired: the name of a CSV file containing a header and 6 columns
        #     description: apply the model to a supplied dataset

        desc = c_meta.get('description', None)
        if desc is not None:
            print("    " + desc)

        c_meta = {k: c_meta[k] for k in c_meta if k != 'description'}
        if len(c_meta) > 0:
            msg = yaml.dump(c_meta, default_flow_style=False)
            msg = msg.split('\n')
            msg = ["    " + ele for ele in msg]
            print('\n'.join(msg), end='')

# ----------------------------------------------------------------------
# Next step suggestion
# ----------------------------------------------------------------------


def get_command_suggestion(cmd, description=None, model=''):
    """Return suggestion about how to use the cmd."""

    if cmd in COMMANDS:
        meta = COMMANDS[cmd]

        # If there is customized suggestion, use it; otherwise
        # generate from description.

        if 'argument' in meta and 'model' in meta['argument'] and model == '':
            model = '<model>'

        msg = meta.get('suggestion',
                       "\nTo " + dropdot(lower_first_letter(meta['description'])) + ":"
                        "\n\n  $ {} {} {}")
        msg = msg.format(CMD, cmd, model)
        return msg

    elif description is not None:
        meta = description['commands'][cmd]

        if type(meta) is str:
            msg = dropdot(lower_first_letter(meta))
        else:
            # Handle malformed DESCRIPTION.yaml like
            # --
            # commands:
            #   print:
            #     description: print a textual summary of the model
            #   score:
            #     required: the name of a CSV file containing a header and 6 columns
            #     description: apply the model to a supplied dataset

            msg = meta.pop('description', None)

        if msg is not None:
            msg = "\nTo " + msg
        else:
            msg = "\nYou may try"
        msg += ":\n\n  $ {} {} {}"
        msg = msg.format(CMD, cmd, model)

        return msg


def print_commands_suggestions_on_stderr(*commands):
    """Print list of suggestions on how to use the command in commands."""

    for cmd in commands:
        print_on_stderr(get_command_suggestion(cmd))

    print_on_stderr('')


def print_next_step(current, description=None, scenario=None, model=''):
    """Print next step suggestions for the command.

    Args:
        current (str): the command needs to be given next step suggestion.
        description(dict): yaml object from DESCRIPTION.yaml
        scenario (str): certain scenario for the next step.
        model (str): the model name if needed.
    """

    if description is None:

        # Use the order for basic commands

        if 'next' not in COMMANDS[current]:
            return

        steps = COMMANDS[current]['next']

        if scenario is not None:
            steps = steps[scenario]

        for next_cmd in steps:
            msg = get_command_suggestion(next_cmd, model=model)
            print(msg)
    else:

        # Use the order in DESCRIPTION.yaml

        avail_cmds = list(description['commands'])

        try:
            next_index = avail_cmds.index(current) + 1 if current != 'commands' else 0
        except ValueError:
            # The command is not described in DESCRIPTION.yaml, ignore it.
            next_index = len(avail_cmds)

        if next_index < len(avail_cmds):
            next_cmd = avail_cmds[next_index]

            msg = get_command_suggestion(next_cmd, description=description, model=model)
        else:
            msg = "\nThank you for exploring the '{}' model.".format(model)

        print(msg)

    print()

# ----------------------------------------------------------------------
# Dependency
# ----------------------------------------------------------------------


def flatten_mlhubyaml_deps(deps, cats=None, res=None):
    """Flatten the hierarchical structure of dependencies in MLHUB.yaml.

    For dependency specification like:

      dependencies:
        system: atril
        R:
          cran: magrittr, dplyr=1.2.3, caret>4.5.6, e1017, httr  # All dependencies in one line
          github:  # One dependency per line
            - rstudio/tfruns
            - rstudio/reticulate
            - rstudio/keras
        python:
          conda: environment.yaml  # Use conda to interpret dependencies
          pip:
            - pillow
            - tools=1.1
        files:
          - https://github.com/mlhubber/colorize/raw/master/configure.sh
          - https://github.com/mlhubber/colorize/raw/master/train.data: data/
          - https://github.com/mlhubber/colorize/raw/master/jsgifd_2018.png: images/cat.png
          - https://github.com/mlhubber/colorize/archive/master.zip: res/
          - https://github.com/mlhubber/colorize/archive/arcdfikdf_12.zip: res/xyz.zip

    Then the input argument <deps> is a dict loaded by yaml from the dependency specification above:

      {'system': 'atril',
       'R': {'cran': 'magrittr, dplyr=1.2.3, caret>4.5.6, e1017, httr',
             'github': ['rstudio/tfruns', 'rstudio/reticulate', 'rstudio/keras']},
       'python': {'conda': 'environment.yaml', 'pip': ['pillow', 'tools=1.1']},
       'files': ['https://github.com/mlhubber/colorize/raw/master/configure.sh',
                 {'https://github.com/mlhubber/colorize/raw/master/train.data': 'data/'},
                 {'https://github.com/mlhubber/colorize/raw/master/jsgifd_2018.png': 'images/cat.png'},
                 {'https://github.com/mlhubber/colorize/archive/master.zip': 'res/'},
                 {'https://github.com/mlhubber/colorize/archive/arcdfikdf_12.zip': 'res/xyz.zip'}]
      }

    And the result returned is something like:

      [[['system'], ['atril']],
       [['r', 'cran'], ['magrittr', 'dplyr=1.2.3', 'caret>4.5.6', 'e1017', 'httr']],
       [['r', 'github'], ['rstudio/tfruns', 'rstudio/reticulate', 'rstudio/keras']],
       [['python', 'conda'], ['environment.yaml']],
       [['python', 'pip'], ['pillow', 'tools=1.1']],
       [['files'], {'https://github.com/mlhubber/colorize/raw/master/configure.sh': None,
                    'https://github.com/mlhubber/colorize/raw/master/train.data': 'data/',
                    'https://github.com/mlhubber/colorize/raw/master/jsgifd_2018.png': 'images/cat.png',
                    'https://github.com/mlhubber/colorize/archive/master.zip': 'res/',
                    'https://github.com/mlhubber/colorize/archive/arcdfikdf_12.zip': 'res/xyz.zip'}]
      ]
    """

    def _dep_split(deps_spec):
        return [x.strip() for x in deps_spec.split(',')]

    def _get_file_target_dict(dep_list):
        results = {}  # TODO: Change to [] instead of {}, in case that the same file needs to be used twice.
        for dep in dep_list:
            if isinstance(dep, str):
                results[dep] = None
            else:
                results.update(dep)
        return results

    if res is None:
        res = []

    if not isinstance(deps, dict):

        if isinstance(deps, str):
            deps = _dep_split(deps)

        res.append([[cats] if cats is None else cats, deps])

    else:

        for category in deps:
            if 'files'.startswith(category):
                if isinstance(deps[category], str):
                    dep_dict = _get_file_target_dict(_dep_split(deps[category]))
                else:
                    dep_dict = _get_file_target_dict(deps[category])
                res.append([['files'], dep_dict])
            else:
                cat_list = [category.lower()] if cats is None else cats + [category.lower()]
                flatten_mlhubyaml_deps(deps[category], cat_list, res)

    return res


def install_r_deps(deps, model, source='cran'):
    script = os.path.join(os.path.dirname(__file__), 'scripts', 'dep', 'r.R')
    command = 'Rscript {} "{}" "{}"'.format(script, source, '" "'.join(deps))

    proc = subprocess.Popen(command, shell=True, cwd=get_package_dir(model), stderr=subprocess.PIPE)
    output, errors = proc.communicate()
    if proc.returncode != 0:
        errors = errors.decode("utf-8")
        command_not_found = re.compile(r"\d: (.*):.*not found").search(errors)
        pkg_not_found = re.compile(r"there is no package called ‘(.*)’").search(errors)
        if command_not_found is not None:
            raise LackPrerequisiteException(command_not_found.group(1))

        if pkg_not_found is not None:
            raise LackPrerequisiteException(pkg_not_found.group(1))

        print("An error was encountered:\n")
        print(errors)
        raise ConfigureFailedException()


def install_python_deps(deps, source='pip'):
    script = os.path.join(os.path.dirname(__file__), 'scripts', 'dep', 'python.sh')
    command = 'bash {} "{}" "{}"'.format(script, source, '" "'.join(deps))

    proc = subprocess.Popen(command, shell=True, stderr=subprocess.PIPE)
    output, errors = proc.communicate()
    if proc.returncode != 0:
        errors = errors.decode("utf-8")
        command_not_found = re.compile(r"\d: (.*):.*not found").search(errors)
        if command_not_found is not None:
            raise LackPrerequisiteException(command_not_found.group(1))

        print("An error was encountered:\n")
        print(errors)
        raise ConfigureFailedException()


def install_system_deps(deps):
    script = os.path.join(os.path.dirname(__file__), 'scripts', 'dep', 'system.sh')
    command = 'bash {} "{}"'.format(script, '" "'.join(deps))

    proc = subprocess.Popen(command, shell=True, stderr=subprocess.PIPE)
    output, errors = proc.communicate()
    if proc.returncode != 0:
        errors = errors.decode("utf-8")
        print("An error was encountered:\n")
        print(errors)
        raise ConfigureFailedException()


def install_file_deps(deps, model, downloadir=None):
    """Install file dependencies.

    For example, if MLHUB.yaml is
    
      files:
        - https://zzz.org/label                        # To package root dir
        - https://zzz.org/cat.RData: data/             # To data/
        - https://zzz.org/def.RData: data/dog.RData    # To data/dog.RData
        - https://zzz.org/xyz.zip:   res/              # Uncompress into res/ and if all files are
                                                       #     under a single top dir, remove the dir
        - https://zzz.org/z.zip:     ./                # The same as above
        - https://zzz.org/uvw.zip:   res/rst.zip       # To res/rst.zip

        - description/README.md                        # To package root dir
        - res/tree.RData:            resource/         # To resource/
        - res/forest.RData:          resource/f.RData  # Change to resource/f.RData
        - images/:                   img               # Change to img
        - audio/:                    resource/         # To resource/audio
        - scripts/*                                    # All files under scripts/ to package's root dir

    <deps> will be:
      {
        'https://zzz.org/label':     None,
        'https://zzz.org/cat.RData': 'data/',
        'https://zzz.org/def.RData': 'data/dog.RData',
        'https://zzz.org/xyz.zip':   'res/',
        'https://zzz.org/z.zip':     './',
        'https://zzz.org/uvw.zip':   'res/rst.zip',

        'description/README.md':     None,
        'res/tree.RData':            'resource/',
        'res/forest.RData':          'resource/f.RData',
        'images/':                   'img',
        'audio/':                    'resource/',
        'scripts/*':                 None,
      }
    
    Then the directory structure will be:

      In archive dir:
        ~/.mlhub/.cache/<pkg>/res/xyz.zip
        ~/.mlhub/.cache/<pkg>/z.zip
    
      In cache dir:
        ~/.mlhub/.cache/<pkg>/label
        ~/.mlhub/.cache/<pkg>/data/cat.RData
        ~/.mlhub/.cache/<pkg>/data/dog.RData
        ~/.mlhub/.cache/<pkg>/res/<files-inside-xyz.zip>
        ~/.mlhub/.cache/<pkg>/<files-inside-z.zip>
        ~/.mlhub/.cache/<pkg>/res/rst.zip
    
      In Package dir:
        ~/.mlhub/<pkg>/label                  --- link-to -->   ~/.mlhub/.cache/<pkg>/label
        ~/.mlhub/<pkg>/data/cat.RData         --- link-to -->   ~/.mlhub/.cache/<pkg>/data/cat.RData
        ~/.mlhub/<pkg>/data/dog.RData         --- link-to -->   ~/.mlhub/.cache/<pkg>/data/dog.RData
        ~/.mlhub/<pkg>/res/<files>            --- link-to -->   ~/.mlhub/.cache/<pkg>/res/<files>
        ~/.mlhub/<pkg>/<files-inside-z.zip>   --- link-to -->   ~/.mlhub/.cache/<pkg>/<files-inside-z.zip>
        ~/.mlhub/<pkg>/res/rst.zip            --- link-to -->   ~/.mlhub/.cache/<pkg>/res/rst.zip

        ~/.mlhub/<pkg>/README.md
        ~/.mlhub/<pkg>/resource/tree.RData
        ~/.mlhub/<pkg>/resource/f.RData
        ~/.mlhub/<pkg>/img
        ~/.mlhub/<pkg>/resource/audio
        ~/.mlhub/<pkg>/<files-inside-scripts>
    """

    # TODO: Add download progress indicator, or use
    #       wget --quiet --show-progress <url> 2>&1
    #
    # TODO: Add support for file type specification, because the file type may
    #       not be determined by URL:
    #
    #         dependencies:
    #           files:
    #             - https://api.github.com/repos/mlhubber/audit/zipball/master
    #               zip: data/
    #
    # TODO: How to deal with different files? Should we download all of them when
    #       'ml install' or separately when 'ml install' for Path, and `ml
    #       configure` for URL (which is by default now)? :
    #
    #         dependencies:
    #           files:
    #             - https://zzz.org/cat.RData: data/  # URL
    #             - cat.jpg: images/                  # Path
    #

    # Setup

    cache_dir = create_package_cache_dir(model)
    archive_dir = create_package_archive_dir(model)
    pkg_dir = get_package_dir(model)

    logger = logging.getLogger(__name__)
    logger.info("Install file dependencies.")
    logger.debug("deps: {}".format(deps))

    if downloadir is None:
        print("\n*** Downloading required files ...")

    for location, target in deps.items():

        # Deal with URL and path differently.
        #
        # If <location> is a path, it is a package file should be installed during `ml install`,
        # elif <location> is a URL, it is a file downloaded during `ml configure`.

        if downloadir is None and is_url(location):  # URL for non-package files

            # Download file into Cache dir, then symbolically link it into Package dir.
            # Thus we can reuse the downloaded files after model package upgrade.

            # Determine file name.

            logger.debug("Download file from URL: {}".format(location))
            filename = get_url_filename(location)
            if filename is None:

                # TODO: The file name cannot be determined from URL.  How to deal with this scenario?
                #       Currently solution: We give it a random name.  This should not occur.

                filename = 'mlhubtmp-' + str(uuid.uuid4().hex)

            # Determine installation path.

            if target is None:  # Download to the top level dir without changing its name.
                target = filename

            if target.endswith(os.path.sep):
                target = os.path.relpath(target) + os.path.sep  # Ensure folder end with '/'
            else:
                target = os.path.relpath(target)

            cache = os.path.join(cache_dir, target)  # Where the file is cached
            if target.endswith(os.path.sep) and not is_archive(filename):  # Download to a dir without changing name
                cache = os.path.join(cache, filename)
                target = os.path.join(target, filename)

            archive = cache  # Where the file is archived, the same as cache if not an archive file
            if target.endswith(os.path.sep) and is_archive(filename):  # Archive needs uncompressed if target is a dir
                archive = os.path.join(archive_dir, target, filename)

            # Download

            download_msg = "\n    * from {}\n        into {} ..."
            confirm_msg = "      The file is cached.  Would you like to download it again"
            print(download_msg.format(location, target))

            needownload = True
            if os.path.exists(archive):
                needownload = yes_or_no(confirm_msg, yes=False)

            if needownload:
                os.makedirs(os.path.dirname(archive), exist_ok=True)

                try:
                    urllib.request.urlretrieve(location, archive)
                except urllib.error.HTTPError:
                    raise ModePkgDependencyFileNotFoundException(location)

            # Install

            src = cache
            dst = os.path.join(pkg_dir, target)
            symlinks = [(src, dst)]
            if target.endswith(os.path.sep) and is_archive(filename):  # Uncompress archive file
                _, _, file_list = unpack_with_promote(archive, cache, remove_dst=False)
                symlinks = [(os.path.join(src, file), os.path.join(dst, file)) for file in file_list]

            for origin, goal in symlinks:
                make_symlink(origin, goal)

        elif downloadir is not None and not is_url(location):  # Path for package files

            # Move the files from download dir to package dir.

            try:
                goal = os.path.join(pkg_dir, '' if target is None else target)
                if location.endswith('*'):  # Move all files under <location> to package's root dir
                    origin = os.path.join(downloadir, location[:-2])
                    merge_folder(origin, goal)
                else:
                    origin = os.path.join(downloadir, location)
                    if os.path.isdir(origin) and not goal.endswith(os.path.sep):
                        merge_folder(origin, goal)
                    else:
                        os.makedirs(os.path.dirname(goal), exist_ok=True)
                        shutil.move(origin, goal)
            except FileNotFoundError:
                raise ModePkgInstallationFileNotFoundException(location)

# ----------------------------------------------------------------------
# GitHub
# ----------------------------------------------------------------------


def is_github_url(name):
    """Check if name starts with http://github.com or https://github.com"""

    if is_url(name):
        domain = name.lower().split('/')[2]
        return domain.endswith("github.com") or domain.endswith("githubusercontent.com")
    else:
        return False


def interpret_github_url(url):
    """Interpret GitHub URL into user name, repo name, branch/blob name.

    The URL may be:

              For master:  mlhubber/mlhub                or  mlhubber/mlhub:doc/MLHUB.yaml
              For branch:  mlhubber/mlhub@dev            or  mlhubber/mlhub@dev:doc/MLHUB.yaml
              For commit:  mlhubber/mlhub@7fad23bdfdfjk  or  mlhubber/mlhub@7fad23bdfdfjk:doc/MLHUB.yaml
        For pull request:  mlhubber/mlhub#15             or  mlhubber/mlhub#15:doc/MLHUB.yaml

    Support for URL like https://github.com/... would not be portable.
    We just leave it in case someone doesn't know how to use Git refs.

              For master:  https://github.com/mlhubber/mlhub
          For repo clone:  https://github.com/mlhubber/mlhub.git
              For branch:  https://github.com/mlhubber/mlhub/tree/dev
             For archive:  https://github.com/mlhubber/mlhub/archive/v2.0.0.zip
                           https://github.com/mlhubber/mlhub/archive/dev.zip
              For a file:  https://github.com/mlhubber/mlhub/blob/dev/DESCRIPTION.yaml
        For pull request:  https://github.com/mlhubber/mlhub/pull/15
    """

    logger = logging.getLogger(__name__)
    logger.info("Interpret GitHub location.")
    logger.debug("url: {}".format(url))

    seg = url.split('/')
    ref = 'master'  # Use master by default.
    yaml_list = [MLHUB_YAML, DESC_YAML, DESC_YML]
    mlhubyaml = None

    if not is_url(url):  # Repo reference like mlhubber/mlhub

        # TODO: Add regexp to validate the url.

        owner = seg[0]
        repo = seg[1]
        if '@' in repo:

            # For branch or commit such as:
            #     mlhubber/mlhub@dev
            #     mlhubber/mlhub@7fad23bdfdfjk
            #     mlhubber/mlhub@dev:doc/MLHUB.yaml

            tmp = repo.split('@')
            repo = tmp[0]
            ref = tmp[1].split(':')[0]

        elif '#' in repo:

            # For pull request such as:
            #     mlhubber/mlhub#15
            #     mlhubber/mlhub#15:doc/MLHUB.yaml

            tmp = repo.split('#')
            repo = tmp[0]
            ref = "pull/" + tmp[1].split(':')[0] + "/head"
        elif ':' in repo:
            repo = repo.split(':')[0]
            mlhubyaml = url.split(':')[-1]

    else:  # Repo URL like https://github.com/mlhubber/mlhub

        owner = seg[3]
        repo = seg[4]
        if repo.endswith(".git"):  # Repo clone url
            repo = repo[:-4]

        if len(seg) >= 7:
            if len(seg) == 7:
                if seg[5] == "archive" and seg[6].endswith(".zip"):  # Archive url
                    ref = seg[6][:-4]
                elif seg[5] == "pull":  # Pull request url
                    ref = "pull/" + seg[6] + "/head"

            else:  # Branch, commit, or specific file
                ref = seg[6]
                mlhubyaml = '/'.join(seg[7:])

    logger.debug("owner: {}, repo: {}, ref: {}, mlhubyaml: {}".format(owner, repo, ref, mlhubyaml))
    return owner, repo, ref, mlhubyaml


def get_pkgzip_github_url(url):
    """Get the GitHub zip file url of model package.

    See https://developer.github.com/v3/repos/contents/#get-archive-link
    """

    owner, repo, ref, _ = interpret_github_url(url)
    # return "https://api.github.com/repos/{}/{}/zipball/{}".format(owner, repo, ref)
    return "https://codeload.github.com/{}/{}/zip/{}".format(owner, repo, ref)


def get_pkgyaml_github_url(url):
    """Get the GitHub url of DESCRIPTION.yaml file of model package.

    See https://developer.github.com/v3/repos/contents/#get-contents
    """

    owner, repo, ref, mlhubyaml = interpret_github_url(url)
    if ref.startswith("pull/"):
        url = "https://api.github.com/repos/{}/{}/contents/{{}}?ref={}".format(owner, repo, ref)
    else:
        url = "https://raw.githubusercontent.com/{}/{}/{}/{{}}".format(owner, repo, ref)
    if mlhubyaml is None:
        return get_available_pkgyaml(url)
    else:
        return url.format(mlhubyaml)

# ----------------------------------------------------------------------
# Model package developer utilities
# ----------------------------------------------------------------------


def get_init_dir():
    """Return the path of MLHUB system folder."""

    return MLINIT


def create_init():
    """Check if the init dir exists and if not then create it."""

    init = get_init_dir()
    return _create_dir(
        init,
        'MLINIT creation failed: {}'.format(init),
        MLInitCreateException(init))


def get_package_name():
    """Return the model pkg name.

    It is used by model pkg developer.
    """

    return os.environ.get('_MLHUB_MODEL_NAME', '')


def get_cmd_cwd():
    """Return the dir where model pkg command is invoked.

    For example, if `cd /temp; ml demo xxx`, then get_cmd_cwd() returns `/temp`.
    It is used by model pkg developer, and is different from where the model pkg script is located.

    `CMD_CWD` is a environment variable passed by mlhub.utils.dispatch() when invoke model pkg script.
    """

    return os.environ.get('_MLHUB_CMD_CWD', '')


def get_package_dir(model=None):
    """Return the dir where the model package should be installed."""

    return os.path.join(get_init_dir(), get_package_name() if model is None else model)


def create_package_dir(model=None):
    """Check existence of dir where the model package is installed, if not create it and return."""

    path = get_package_dir(model)

    return _create_dir(
        path,
        'Model package dir creation failed: {}'.format(path),
        ModelPkgDirCreateException(path))


def get_package_cache_dir(model=None):
    """Return the dir where the model package stores cached files, such as pre-built model, data, image files, etc."""

    return os.path.join(CACHE_DIR, get_package_name() if model is None else model)


def create_package_cache_dir(model=None):
    """Check existence of dir where the model package stores cached files, If not create it and return."""

    path = get_package_cache_dir(model)

    return _create_dir(
        path,
        'Model package cache dir creation failed: {}'.format(path),
        ModelPkgCacheDirCreateException(path))


def get_package_archive_dir(model=None):
    """Return the dir where the model package stores cached archived files."""

    return os.path.join(ARCHIVE_DIR, get_package_name() if model is None else model)


def create_package_archive_dir(model=None):
    """Check existence of dir where the model package stores cached archived files, If not create it and return."""

    path = get_package_archive_dir(model)

    return _create_dir(
        path,
        'Model package archive dir creation failed: {}'.format(path),
        ModelPkgArchiveDirCreateException(path))


def gen_packages_yaml(mlmodelsyaml='MLMODELS.yaml', packagesyaml='Packages.yaml'):
    """Generate Packages.yaml, the curated list of model packages, by just concatenate all MLHUB.yaml.
    By default, it will generate Packages.yaml in current working dir.

    Args:
        mlmodelsyaml (str): YAML file which list all available models and their location.
        packagesyaml (str): YAML file which will hold meta data in all MLHUB.yaml.
    """

    entry = yaml.load(open(mlmodelsyaml))
    model_list = list(entry.keys())
    model_list.sort()
    failed_models = []

    with open(packagesyaml, 'w') as file:
        for model in model_list:

            # Write yaml entry separator

            file.write("--- # {}\n".format(model))

            # Read model's MLHUB.yaml file

            try:
                location = entry[model]
                mlhubyaml = get_pkgyaml_github_url(location)
                print("Reading {}'s MLHUB.yaml file from {} ...".format(model, mlhubyaml))
                res = json.loads(urllib.request.urlopen(mlhubyaml).read())
            except (urllib.error.HTTPError, DescriptionYAMLNotFoundException):
                failed_models.append(model)
                continue

            content = base64.b64decode(res["content"]).decode()

            for line in content.splitlines():

                # Remove yaml entry separator in model's MLHUB.yaml to avoid duplication

                if line.startswith('---') or line.startswith('...'):
                    continue

                file.write(line)
                file.write('\n')

    if len(failed_models) != 0:
        print("Failed to curate list for models:\n    {}".format(', '.join(failed_models)))


def gen_packages_yaml2(mlmodelsyaml='MLMODELS.yaml', packagesyaml='Packages.yaml'):
    """Generate Packages.yaml, the curated list of model packages, using yaml to ensure correct format.
    By default, it will generate Packages.yaml in current working dir.

    Args:
        mlmodelsyaml (str): YAML file which list all available models and their location.
        packagesyaml (str): YAML file which will hold meta data in all MLHUB.yaml.
    """

    meta = yaml.load(open(mlmodelsyaml))
    model_list = list(meta.keys())
    model_list.sort()
    failed_models = []

    with open(packagesyaml, "w") as file:
        entry_list = []
        for model in model_list:

            # Read model's MLHUB.yaml file

            try:
                location = meta[model]
                mlhubyaml = get_pkgyaml_github_url(location)
                print("Reading {}'s MLHUB.yaml file from {} ...".format(model, mlhubyaml))
                res = json.loads(urllib.request.urlopen(mlhubyaml).read())
            except (urllib.error.HTTPError, DescriptionYAMLNotFoundException):
                failed_models.append(model)
                continue

            content = base64.b64decode(res["content"]).decode()

            try:
                entry = yaml.load(content, Loader=yamlordereddictloader.Loader)
            except (yaml.composer.ComposerError, yaml.scanner.ScannerError):
                failed_models.append(model)
                continue

            entry_list.append(entry)

        yaml.dump_all(entry_list, file)

    if len(failed_models) != 0:
        print("Failed to curate list for models:\n    {}".format(', '.join(failed_models)))

# ----------------------------------------------------------------------
# Bash completion helper
# ----------------------------------------------------------------------


def create_completion_dir():
    """Check if the init dir exists and if not then create it."""

    return _create_dir(
        COMPLETION_DIR,
        'Bash completion dir creation failed: {}'.format(COMPLETION_DIR),
        CompletionDirCreateException(COMPLETION_DIR))


def update_completion_list(completion_file, new_words):
    """Update specific completion list.
    Args:
        completion_file (str): full path of the completion file
        new_words (set): set of new words
    """

    logger = logging.getLogger(__name__)
    logger.info('Update bash completion cache.')
    logger.debug('Completion file: {}'.format(completion_file))
    logger.debug('New completion words: {}'.format(new_words))

    create_completion_dir()

    if os.path.exists(completion_file):
        with open(completion_file, 'r') as file:
            old_words = {line.strip() for line in file if line.strip()}
            logger.debug('Old Completion words: {}'.format(old_words))

        words = old_words | new_words
    else:
        words = new_words

    logger.debug('All completion words: {}'.format(words))
    with open(completion_file, 'w') as file:
        file.write('\n'.join(words))


def update_model_completion(new_words):
    """Update bash completion cache list for models."""

    update_completion_list(COMPLETION_MODELS, new_words)


def update_command_completion(new_words):
    """Update bash completion cache list for commands."""

    update_completion_list(COMPLETION_COMMANDS, new_words)


def get_completion_list(completion_file):
    """Get the list of available words from cached completion file."""

    words = set()
    if os.path.exists(completion_file):
        with open(completion_file) as file:
            words = {line.strip() for line in file if line.strip()}

    # print('\n'.join(words))

    return list(words)


def get_command_completion_list():
    """Get cached available commands."""

    return get_completion_list(COMPLETION_COMMANDS)


def get_model_completion_list():
    """Get cached available model pkg names."""

    return get_completion_list(COMPLETION_MODELS)

# -----------------------------------------------------------------------
# Fuzzy match helper
# -----------------------------------------------------------------------


def find_best_match(misspelled, candidates):
    """Find the best matched word with <misspelled> in <candidates>."""

    best_match = fuzzprocess.extract(misspelled, candidates, scorer=fuzz.ratio)[0]
    matched = best_match[0]
    score = best_match[1]

    return matched, score


def is_misspelled(score):
    """Check misspelled in terms of score."""

    return score >= 80 and score != 100  # 80 is an empirical value.


def get_misspelled_command(command, available_commands):

    matched, score = find_best_match(command, available_commands)
    if is_misspelled(score):
        yes = yes_or_no("The command '{}' is not supported.  Did you mean '{}'", command, matched, yes=True)
        if yes:
            print()
            return matched

    return None


def get_misspelled_pkg(model):

    model_completion_list = get_model_completion_list()
    if len(model_completion_list) != 0:
        matched, score = find_best_match(model, model_completion_list)
        if is_misspelled(score):
            yes = yes_or_no("The model '{}' was not found.  Did you mean '{}'", model, matched, yes=True)
            if yes:
                print()
                return matched

    return None


# -----------------------------------------------------------------------
# Command line argument parse helper
# -----------------------------------------------------------------------


class SubCmdAdder(object):
    """Add the subcommands described in <commands> into <subparsers> with
corresponding functions defined in <module>."""

    def __init__(self, subparsers, module, commands):
        """
        Args:
            subparsers (argparse._SubParsersAction): to which the subcommand is added.
            module: the module which defines the actual function for the subcommand.
            commands (dict): meta info for the subcommand.
        """
        self.subparsers = subparsers
        self.module = module
        self.commands = commands
        self.logger = logging.getLogger(__name__)

    def add_subcmd(self, subcommand):
        """Add <subcommand> to subparsers."""

        cmd_meta = self.commands[subcommand]
        self.logger.debug("Add command line positioanl argument: {} - {}".format(subcommand, cmd_meta))

        parser = self.subparsers.add_parser(
            subcommand,
            aliases=cmd_meta.get('alias', ()),
            description=cmd_meta['description'],
        )

        if 'argument' in cmd_meta:
            args = cmd_meta['argument']
            for name in args:
                parser.add_argument(name, **args[name])

        if 'func' in cmd_meta:
            parser.set_defaults(func=getattr(self.module, cmd_meta['func']))

    def add_allsubcmds(self):
        """Add all subcommands described in <self.commands> into <self.subparsers>."""

        for cmd in self.commands:
            self.add_subcmd(cmd)


class OptionAdder(object):
    """Add the global options described in <options> into <parser>."""

    def __init__(self, parser, options):
        self.parser = parser
        self.options = options
        self.logger = logging.getLogger(__name__)

    def add_option(self, option):
        opt = self.options[option]
        opt_alias = [option, ]
        if 'alias' in opt:
            opt_alias += opt['alias']
            del opt['alias']
        self.logger.debug("Add command line optional argument: {} - {}".format(opt_alias, opt))
        self.parser.add_argument(*opt_alias, **opt)

    def add_alloptions(self):
        for opt in self.options:
            self.add_option(opt)

# ----------------------------------------------------------------------
# Debug Log and Error Printing
# ----------------------------------------------------------------------


def create_log_dir():
    """Check if the log dir exists and if not then create it."""

    return _create_dir(
        LOG_DIR,
        'Log dir creation failed: {}'.format(LOG_DIR),
        LogDirCreateException(LOG_DIR))


def add_log_handler(logger, handler, level, fmt):
    """Add handler with level and format to logger"""

    handler.setLevel(level)
    formatter = logging.Formatter(fmt)
    handler.setFormatter(formatter)
    logger.addHandler(handler)


def print_on_stderr(msg, *param):
    """Print msg on stderr"""

    print(msg.format(*param), file=sys.stderr)


def print_on_stderr_exit(msg, *param, exitcode=1):
    """Print msg on stderr and exit."""

    print_on_stderr(msg, *param)
    sys.exit(exitcode)


def print_error(msg, *param):
    """Print error msg with APPX prefix on stderr."""

    print_on_stderr("\n" + APPX + msg.format(*param))


def print_error_exit(msg, *param, exitcode=1):
    """Print error msg with APPX prefix on stderr and exit."""

    print_error(msg, *param)
    sys.exit(exitcode)

# ----------------------------------------------------------------------
# Misc
# ----------------------------------------------------------------------


def configure(path, script, quiet):
    """Run the provided configure scripts and handle errors and output."""

    configured = False

    # For now only tested/working with Ubuntu

    if distro.id() in ['debian', 'ubuntu']:
        conf = os.path.join(path, script)
        if os.path.exists(conf):
            interp = interpreter(script)
            if not quiet:
                msg = "\nConfiguring using '{}'...\n".format(conf)
                print(msg)
            cmd = "export _MLHUB_CMD_CWD='{}'; export _MLHUB_MODEL_NAME='{}'; {} {}".format(
                os.getcwd(), os.path.basename(path), interp, script)
            logger = logging.getLogger(__name__)
            logger.debug("(cd " + path + "; " + cmd + ")")
            proc = subprocess.Popen(cmd, shell=True, cwd=path, stderr=subprocess.PIPE)
            output, errors = proc.communicate()
            if proc.returncode != 0:
                errors = errors.decode("utf-8")
                logger.error("Configure failed: \n{}".format(errors))
                print("An error was encountered:\n")
                print(errors)
                raise ConfigureFailedException()
            configured = True

    return configured


def interpreter(script):
    """Determine the correct interpreter for the given script name."""

    (root, ext) = os.path.splitext(script)
    ext = ext.strip()
    if ext == ".sh":
        intrprt = "bash"
    elif ext == ".R":
        intrprt = "R_LIBS=./R Rscript"
    elif ext == ".py":
        intrprt = "python3"
    else:
        raise UnsupportedScriptExtensionException(ext)

    return intrprt


def yes_or_no(msg, *params, yes=True):
    """Query yes or no with message.

    Args:
        msg (str): Message to be printed out.
        yes (bool): Indicates whether the default answer is yes or no.
    """

    print(msg.format(*params) + (' [Y/n]?' if yes else ' [y/N]?'), end=' ')
    choice = input().lower()

    answer = True if yes else False

    if yes and choice == 'n':
        answer = False

    if not yes and choice == 'y':
        answer = True

    return answer

# ----------------------------------------------------------------------
# Custom Exceptions
# ----------------------------------------------------------------------


class ModelURLAccessException(Exception):
    pass


class ModelNotFoundOnRepoException(Exception):
    pass


class MalformedMLMFileNameException(Exception):
    pass


class RepoAccessException(Exception):
    pass


class MLInitCreateException(Exception):
    pass


class CompletionDirCreateException(Exception):
    pass


class DescriptionYAMLNotFoundException(Exception):
    pass


class ModelDownloadHaltException(Exception):
    pass


class ModelNotInstalledException(Exception):
    pass


class ModelReadmeNotFoundException(Exception):
    pass


class UnsupportedScriptExtensionException(Exception):
    pass


class CommandNotFoundException(Exception):
    pass


class LogDirCreateException(Exception):
    pass


class ModelPkgDirCreateException(Exception):
    pass


class ModelPkgCacheDirCreateException(Exception):
    pass


class LackDependencyException(Exception):
    pass


class LackPrerequisiteException(Exception):
    pass


class ConfigureFailedException(Exception):
    pass


class DataResourceNotFoundException(Exception):
    pass


class MLTmpDirCreateException(Exception):
    pass


class MalformedYAMLException(Exception):
    pass


class YAMLFileAccessException(Exception):
    pass


class MalformedPackagesDotYAMLException(Exception):
    pass


class ModelPkgArchiveDirCreateException(Exception):
    pass


class ModePkgInstallationFileNotFoundException(Exception):
    pass


class ModePkgDependencyFileNotFoundException(Exception):
    pass
