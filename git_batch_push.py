#!/usr/bin/env python3
"""Batch commit and push untracked files on the current Git branch."""
import os
import subprocess
import sys
from pathlib import Path

BATCH_LIMIT = 500 * 1024 * 1024
FILE_LIMIT = 100 * 1024 * 1024


def git(*args, check=True):
    return subprocess.run(['git', *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check)


def output(*args):
    return git(*args).stdout.decode('utf-8', 'replace').strip()


def main():
    root = Path(output('rev-parse', '--show-toplevel'))
    os.chdir(root)
    branch = output('branch', '--show-current')
    if not branch:
        sys.exit('ERROR: Detached HEAD; switch to a branch first.')
    if output('diff', '--name-only') or output('diff', '--cached', '--name-only'):
        sys.exit('ERROR: Tracked modifications or staged changes exist; handle these first.')
    remote = git('remote', 'get-url', 'origin', check=False)
    if remote.returncode:
        sys.exit('ERROR: No origin remote configured.')
    upstream = git('ls-remote', '--heads', 'origin', branch, check=False)
    if upstream.returncode:
        sys.exit('ERROR: Could not check remote branch; verify connectivity/authentication.')
    if upstream.stdout:
        remote_sha = upstream.stdout.decode().split()[0]
        local_head = git('rev-parse', '--verify', 'HEAD', check=False)
        if local_head.returncode:
            sys.exit('ERROR: Remote branch exists but local branch has no commits.')
        ancestor = git('merge-base', '--is-ancestor', remote_sha, 'HEAD', check=False)
        if ancestor.returncode:
            sys.exit('ERROR: Remote branch has different history. Refusing to overwrite.')
        if output('rev-parse', 'HEAD') != remote_sha:
            print('Pushing previously committed batch first...', flush=True)
            if subprocess.run(['git', 'push', '-u', 'origin', branch]).returncode:
                sys.exit('ERROR: Pending push failed; retry later.')

    data = git('ls-files', '--others', '--exclude-standard', '-z').stdout
    files = [os.fsdecode(x) for x in data.split(b'\0') if x]
    files.sort()
    batches, batch, total = [], [], 0
    skipped = []
    for name in files:
        p = Path(name)
        if p.is_symlink():
            size = 0
        elif p.is_file():
            size = p.stat().st_size
        else:
            continue
        if size > FILE_LIMIT:
            skipped.append((name, size))
            continue
        if batch and total + size > BATCH_LIMIT:
            batches.append(batch)
            batch, total = [], 0
        batch.append(name)
        total += size
    if batch:
        batches.append(batch)
    print(f'Branch: {branch}; eligible files: {sum(map(len,batches))}; batches: {len(batches)}; oversized skipped: {len(skipped)}', flush=True)
    if skipped:
        print('Oversized files (need Git LFS or exclusion):', flush=True)
        for name, size in skipped:
            print(f'  {size/1024/1024:.1f} MiB  {name}', flush=True)
    if not batches:
        return
    if input('Type YES to begin committing and pushing: ').strip() != 'YES':
        print('Cancelled.')
        return
    for i, group in enumerate(batches, 1):
        print(f'Batch {i}/{len(batches)}: staging {len(group)} files', flush=True)
        # stdin NUL-delimited avoids command-line length limits and handles unusual filenames
        payload = b'\0'.join(os.fsencode(p) for p in group) + b'\0'
        stage = subprocess.run(['git', 'add', '--pathspec-from-file=-', '--pathspec-file-nul'], input=payload)
        if stage.returncode:
            sys.exit('Staging failed; inspect git status.')
        if subprocess.run(['git', 'commit', '-m', f'Upload batch {i}']).returncode:
            sys.exit('Commit failed; inspect git status.')
        push = subprocess.run(['git', 'push', '-u', 'origin', branch])
        if push.returncode:
            sys.exit('Push failed. STOP: run git push -u origin ' + branch + ' to retry this commit before anything else.')
    print('All eligible files uploaded successfully.')


if __name__ == '__main__':
    main()
