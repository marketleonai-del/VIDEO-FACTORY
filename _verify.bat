@echo off
cd /d "%~dp0"
echo === git log (local) === > _verify.txt 2>&1
git log --oneline -3 >> _verify.txt 2>&1
echo. >> _verify.txt 2>&1
echo === git remote === >> _verify.txt 2>&1
git remote -v >> _verify.txt 2>&1
echo. >> _verify.txt 2>&1
echo === git status === >> _verify.txt 2>&1
git status -sb >> _verify.txt 2>&1
echo. >> _verify.txt 2>&1
echo === git ls-remote origin (what GitHub actually has) === >> _verify.txt 2>&1
git ls-remote origin >> _verify.txt 2>&1
echo. >> _verify.txt 2>&1
echo === DONE === >> _verify.txt 2>&1
