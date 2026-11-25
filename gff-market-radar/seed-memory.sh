#!/usr/bin/env bash
# بيرفع ملفات agents/gff-market-radar/data/ لمخزن ذاكرة رادار بنفس المسارات.
# مثال: data/segments/segments.md بيترفع على /segments/segments.md
# يتشغل من أي مكان بعد `ant apply` للمخزن (من فولدر gff-market-radar/ عشان claude-lock.json يتكتب هناك).
# لو وقف في النص، شغّله تاني: بيتخطى الملفات اللي اترفعت قبل كده.
# أي تعديل على ملف موجود بعد كده بيكون بأمر `ant beta:memory-stores:memories update`، لأن create على نفس المسار بيرجّع 409.
# السكريبت ده ماتجربش على حساب حقيقي، فراجعوا الناتج.
set -uo pipefail
cd "$(dirname "$0")"

if [ ! -f claude-lock.json ]; then
  echo "مالقيتش claude-lock.json - شغّل ant apply للمخزن الأول من فولدر gff-market-radar/" >&2
  exit 1
fi
STORE_ID=$(jq -r '.resources["./agents/gff-market-radar/memory_store-market-knowledge.yaml"].id // empty' claude-lock.json)
if [ -z "$STORE_ID" ]; then
  echo "مالقيتش ID المخزن في claude-lock.json - شغّل ant apply للمخزن الأول" >&2
  exit 1
fi

EXISTING=$(ant beta:memory-stores:memories list --memory-store-id "$STORE_ID" --transform path -r 2>/dev/null || true)
FAILED=0
while read -r f; do
  p="/${f#agents/gff-market-radar/data/}"
  if grep -qxF "$p" <<<"$EXISTING"; then
    echo "موجود قبل كده: $p"
    continue
  fi
  echo "رفع $f -> $p"
  if ! printf 'content: "@./%s"\n' "$f" | ant beta:memory-stores:memories create --memory-store-id "$STORE_ID" --path "$p" >/dev/null; then
    echo "فشل: $p" >&2
    FAILED=1
  fi
done < <(find agents/gff-market-radar/data -type f -name '*.md' | sort)

# تأكد إن المحتوى اللي اترفع هو نص الملف، مش المسار نفسه
ant beta:memory-stores:memories list --memory-store-id "$STORE_ID" --view full > seed-check.txt 2>&1 || FAILED=1
if grep -q '@\./agents/' seed-check.txt; then
  echo "تحذير: فيه ملف اترفع محتواه كمسار مش كنص - راجع seed-check.txt" >&2
  FAILED=1
fi
echo "عدد الملفات المحلية: $(find agents/gff-market-radar/data -type f -name '*.md' | wc -l) - راجع seed-check.txt للتأكد إن كلهم موجودين بمحتواهم"
exit $FAILED
