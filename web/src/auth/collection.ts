// "My Collection" = the shared community feed filtered to one collector.
// Every published post carries the scanner's hive_display_key in its
// attribution block (the post author is always the shared app account).

import type { Attribution, BinderCard } from '../binderTypes'

function attributionOf(entry: BinderCard): Attribution {
  // Feed entries expose the block top-level; older cached shapes only have
  // the copy embedded in the card record.
  return entry.attribution ?? entry.card.attribution
}

export function isMine(entry: BinderCard, hiveDisplayKey: string): boolean {
  if (!hiveDisplayKey) return false
  return attributionOf(entry).hive_display_key === hiveDisplayKey
}

export function filterMine(feed: BinderCard[], hiveDisplayKey: string): BinderCard[] {
  return feed.filter(entry => isMine(entry, hiveDisplayKey))
}
