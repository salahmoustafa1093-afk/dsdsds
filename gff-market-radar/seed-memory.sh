#!/usr/bin/env bash
# بيرفع ملفات agents/gff-market-radar/data/ لمخزن ذاكرة رادار بنفس المسارات.
# مثال: data/segments/segments.md بيترفع على /segments/segments.md
# يتشغل من فولدر gff-market-radar/ بعد `ant apply` للمخزن، ومرة واحدة بس.
# أي تعديل بعد كده بيكون بأمر `ant beta:memory-stores:memories update`، لأن create على نفس المسار بيرجّع 409.
# السكريبت ده ماتجربش على حساب حقيقي، فراجعوا أول ناتج قبل ما تكملوا.
set -euo pipefail
cd "$(dirname "$0")"

STORE_ID=$(jq -r '.resources["./agents/gff-market-radar/memory_store-market-knowledge.yaml"].id' claude-lock.json)
if [ -z "$STORE_ID" ] || [ "$STORE_ID" = "null" ]; then
  echo "مالقيتش ID المخزن في claude-lock.json - شغّل ant apply للمخزن الأول" >&2
  exit 1
fi

find agents/gff-market-radar/data -type f -name '*.md' | sort | while read -r f; do
  p="/${f#agents/gff-market-radar/data/}"
  echo "رفع $f -> $p"
  printf 'content: "@./%s"\n' "$f" | ant beta:memory-stores:memories create --memory-store-id "$STORE_ID" --path "$p"
done

echo "اتأكد إن المحتوى نص الملف مش المسار:"
ant beta:memory-stores:memories list --memory-store-id "$STORE_ID" --view full | head -60
