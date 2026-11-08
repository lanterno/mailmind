// A record's entries with its keys' own type, in its order. Object.entries says string, which loses a closed set's union.
export const entries = <K extends string, V>(record: Record<K, V>) => Object.entries(record) as [K, V][];
