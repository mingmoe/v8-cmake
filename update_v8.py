#!/usr/bin/env python

from os.path import abspath, dirname, exists, join
import argparse
import json
import os
import re
import subprocess
import sys
import shutil
from typing import *

class colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

DATABASE = 'update_v8.json'
LOCKFILE_NAME = f'{DATABASE}.lock'

CURRENT_WORKSPACE = abspath(dirname(__file__))
CURRENT_TEMPDIR = abspath(join(CURRENT_WORKSPACE,"./temp"))
DEFAULT_GIT = shutil.which("git") if shutil.which("git") is not None else "git"
DEFAULT_TAR = shutil.which("tar") if shutil.which("tar") is not None else "tar"

options = None

os.makedirs(CURRENT_WORKSPACE, exist_ok=True)
os.makedirs(CURRENT_TEMPDIR, exist_ok=True)

# ensure we are in python3
if sys.version_info[0] < 3:
  print("python2 is not supported!")
  print("please use python3 instead")
  sys.exit(1)

def git(*args, **kwargs):
  cmd = [options.git] + list(args)
  print(' '.join(cmd))

  try:
    if kwargs.pop('dry_run'): return
  except KeyError:
    pass

  if kwargs.get('check_output'):
    del kwargs['check_output']
    kwargs['encoding'] = 'utf-8'
    output = subprocess.check_output(cmd, **kwargs).strip()
    print(f"{colors.OKGREEN}\n{output}{colors.ENDC}\n")
    return output

  output = subprocess.check_output(cmd, **kwargs).strip()
  print(f"{colors.OKGREEN}\n{output}{colors.ENDC}\n")

def isv8(dep) -> bool:
  return '' == dep['path']

def repodir(dep) -> str:
  return abspath(join(options.tmpdir, 'v8', dep['path'].replace('/', '_')))

def repodir_exists(dep) -> bool:
  return exists(join(repodir(dep), 'config'))

def update_one(dep):
  cwd = abspath('.')

  url = dep['url']
  path = dep['path']
  branch = dep['branch']
  commit = dep['commit']
  clonedir = repodir(dep)

  if not repodir_exists(dep):
    git('clone', '--bare', url, clonedir, cwd=options.tmpdir, dry_run=options.dry_run)

  what = commit
  if isv8(dep):
    what = '+refs/{}:refs/{}'.format(branch, branch)

  git('fetch', url, what, cwd=clonedir, dry_run=options.dry_run)


def update_all():
  with open(DATABASE) as fp:
    deps = json.load(fp)

  assert len(deps) > 0
  assert isinstance(deps, list)

  for dep in deps:
    assert isinstance(dep, dict)
    assert isinstance(dep.get('branch'), str)
    assert isinstance(dep.get('commit'), str)
    assert isinstance(dep.get('path'), str)
    assert isinstance(dep.get('url'), str)

  v8 = deps[0] # must be first
  assert isv8(v8)

  update_one(v8)
  v8['commit'] = (
      git('rev-parse', v8['branch'], check_output=True, cwd=repodir(v8)))

  # Now for some arbitrary code execution...
  what = '{}:DEPS'.format(v8['commit'])
  source = git('show', what, check_output=True, cwd=repodir(v8))
  code = compile('def Var(k): return vars[k]\ndef Str(k): return str(k)\n' + source, 'DEPS', 'exec')
  globls = {}
  eval(code, globls)
  v8_deps = globls['deps']
  assert isinstance(v8_deps, dict)

  for dep in deps[1:]: # skip v8 itself
    changed = options.force or not repodir_exists(dep)

    path = dep['path']
    url_and_commit = v8_deps.get(path)
    if not url_and_commit:
      raise Exception('{} missing from DEPS'.format(path))
    if isinstance(url_and_commit, dict):
      url_and_commit = url_and_commit.get('url')
    if not isinstance(url_and_commit, str):
      raise Exception('{} is not a string or dict in DEPS'.format(path))
    url, commit = url_and_commit.split('@', 2)

    if url != dep['url']:
      print('url changed: {} -> {}'.format(dep['url'], url))
      dep['url'] = url
      changed = True

    if commit != dep['commit']:
      print('commit changed: {} -> {}'.format(dep['commit'], commit))
      dep['commit'] = commit
      changed = True

    if changed:
      update_one(dep)

  arg = '--dry-run' if options.dry_run else '-q'
  git('rm', arg, '-r','-f','--ignore-unmatch', './v8')

  for dep in deps:
    cmd = [options.git,"archive","--format=tar",f"--prefix=v8/{dep['path']}/",dep['commit'],"-o",f"{CURRENT_TEMPDIR}/v8-archive.tar"]
    print(cmd)
    if not options.dry_run:
      subprocess.check_call(cmd, shell=False,cwd=repodir(dep))

    cmd = [options.tar,"xf",f"{CURRENT_TEMPDIR}/v8-archive.tar"]
    print(cmd)
    if not options.dry_run:
      subprocess.check_call(cmd, shell=False)

  # apply patches
  for filename in sorted(os.listdir('patches')):
    if filename.endswith('.patch'):
      git('apply', '--reject', join('patches', filename), dry_run=options.dry_run)

  # remove compiled file
  for path, _, files in os.walk('v8'):
    for filename in files:
      if filename.endswith('.pyc'):
        os.remove(join(path, filename))

  # update v8 in this rope
  git('add', '-f', 'v8', dry_run=options.dry_run)
  git('log', '-1', '--oneline', v8['commit'], cwd=repodir(v8))

  # update database
  newdeps = json.dumps(deps, indent=2)
  newdeps = re.sub(r'\s+$', '\n', newdeps)
  if options.dry_run:
    print(newdeps)
  else:
    with open(DATABASE, 'w') as fp:
      fp.write(newdeps)


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description='Sync with upstream V8')
  parser.add_argument('--dry-run', default=False, action='store_true')
  parser.add_argument('--force', default=False, action='store_true')
  parser.add_argument('--git', default=DEFAULT_GIT)
  parser.add_argument('--tar', default=DEFAULT_TAR)
  options = parser.parse_args()

  options.force = False
  options.workspace = CURRENT_WORKSPACE
  options.tmpdir = CURRENT_TEMPDIR

  os.chdir(options.workspace)

  with open(LOCKFILE_NAME, 'r') as lockfile:
    update_all()
