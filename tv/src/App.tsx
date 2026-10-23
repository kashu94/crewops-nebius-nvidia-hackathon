import React, { useEffect, useState } from 'react';
import { BackHandler, ScrollView, Text, View } from 'react-native';
import { TVFocusGuideView } from '@amazon-devices/react-native-kepler';

import { DetailPanel } from './components/DetailPanel';
import { DisruptionCard } from './components/DisruptionCard';
import { AllDesksButton, DeskButton } from './components/DeskButton';
import { LatestRail } from './components/LatestRail';
import { useBoard } from './useBoard';
import { AIRLINE, colors, LEVEL, useScale } from './theme';
import type { Card } from './types';

// The CrewOps ops-room wall display: what a crew control room puts on its big screen.
// The most serious and escalated disruptions first, every desk's load, and today's
// operation at a glance; the remote's D-pad moves between disruptions and OK opens one.

function clock(offsetMinutes: number): string {
  const d = new Date(Date.now() + offsetMinutes * 60_000);
  return `${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}`;
}

const RANKS = [
  ['Captain', 'CPT'],
  ['First Officer', 'FO'],
  ['Senior Cabin Crew', 'SCC'],
  ['Cabin Crew', 'CC'],
] as const;

/** Reserve cover as one row per airline and base, every rank shown (a zero is a gap). */
function coverRows(cover: { airline: string; base: string; rank: string; on_call: number }[]) {
  const rows = new Map<string, { key: string; airline: string; base: string; counts: Record<string, number> }>();
  for (const r of cover) {
    const key = `${r.airline}-${r.base}`;
    const row = rows.get(key) ?? { key, airline: r.airline, base: r.base, counts: {} };
    row.counts[r.rank] = (row.counts[r.rank] ?? 0) + r.on_call;
    rows.set(key, row);
  }
  return [...rows.values()];
}

/** The opened disruption as the board now shows it; null once it is no longer on it. */
export function openedCard(cards: Card[] | undefined, id: string | null): Card | null {
  return id ? ((cards ?? []).find((c) => c.disruption_id === id) ?? null) : null;
}

function useNow(): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 15_000);
    return () => clearInterval(t);
  }, []);
  return now;
}

export const App = () => {
  const { board, error, updatedAt } = useBoard();
  // The disruption opened with OK, by id: the panel always shows the latest board's copy,
  // and closes once the disruption has left the board (resolved, closed).
  const [openId, setOpenId] = useState<string | null>(null);
  // OK on a desk on the left shows only that desk's disruptions.
  const [deskFilter, setDeskFilter] = useState<string | null>(null);
  const now = useNow();
  const s = useScale();

  const open = openedCard(board?.disruptions, openId);
  const isOpen = open !== null;

  // The remote's back button closes an open disruption before it leaves the app.
  useEffect(() => {
    const sub = BackHandler.addEventListener('hardwareBackPress', () => {
      if (isOpen) {
        setOpenId(null);
        return true;
      }
      return false;
    });
    return () => sub.remove();
  }, [isOpen]);

  const chosen = board?.desks.find((d) => d.desk_id === deskFilter) ?? null;
  const shown = (board?.disruptions ?? []).filter((c) => !deskFilter || c.desk?.desk_id === deskFilter);
  const stale = Boolean(error) || (updatedAt !== null && now - updatedAt > 90_000);
  const label = { color: colors.muted, fontSize: s(15), fontWeight: '600' as const, letterSpacing: s(1.5) };
  const tile = {
    backgroundColor: colors.panel,
    borderRadius: s(14),
    borderWidth: s(1),
    borderColor: colors.line,
    paddingVertical: s(12),
    paddingHorizontal: s(18),
    marginRight: s(12),
  };

  return (
    <View style={{ flex: 1, backgroundColor: colors.bg, paddingHorizontal: s(56), paddingTop: s(36), paddingBottom: s(24) }}>
      {/* Header: what this is, and whether it is current. */}
      <View style={{ flexDirection: 'row', alignItems: 'flex-end', justifyContent: 'space-between' }}>
        <View>
          <Text style={{ ...label, color: colors.sky }}>CREWOPS  ·  OPS ROOM</Text>
          <Text style={{ color: colors.text, fontSize: s(34), fontWeight: '600', marginTop: s(4) }}>Crew disruptions</Text>
        </View>
        <View style={{ flexDirection: 'row', alignItems: 'baseline' }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', marginRight: s(28) }}>
            <View
              style={{ width: s(10), height: s(10), borderRadius: s(5), marginRight: s(8), backgroundColor: stale ? colors.amber : colors.green }}
            />
            <Text style={{ color: colors.muted, fontSize: s(18) }}>{!board ? 'Connecting' : stale ? 'Reconnecting' : 'Live'}</Text>
          </View>
          <Text style={{ color: colors.text, fontSize: s(34), fontWeight: '600' }}>{clock(0)}</Text>
          <Text style={{ color: colors.muted, fontSize: s(18), marginLeft: s(6), marginRight: s(18) }}>UTC</Text>
          <Text style={{ color: colors.muted, fontSize: s(22) }}>{clock(330)} IST</Text>
        </View>
      </View>

      {board && <LatestRail events={board.latest} />}

      {board && board.counts.escalated > 0 && (
        <View
          style={{
            marginTop: s(18),
            backgroundColor: colors.redDeep,
            borderColor: colors.red,
            borderWidth: s(1),
            borderLeftWidth: s(6),
            borderRadius: s(12),
            paddingVertical: s(10),
            paddingHorizontal: s(18),
          }}>
          <Text style={{ color: '#ffd5d5', fontSize: s(20), fontWeight: '600' }}>
            {board.counts.escalated} escalated to the duty supervisor: waiting on a desk with nothing done yet
          </Text>
        </View>
      )}

      {!board ? (
        <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center' }}>
          <Text style={{ color: colors.muted, fontSize: s(24) }}>
            {error ? `Cannot reach CrewOps: ${error}` : 'Reading the operation…'}
          </Text>
        </View>
      ) : (
        <>
          {/* One strip: how serious, and today's operation. */}
          <View style={{ flexDirection: 'row', marginTop: s(22) }}>
            {(['high', 'medium', 'low'] as const).map((level) => (
              <View key={level} style={{ ...tile, flexDirection: 'row', alignItems: 'center' }}>
                <View style={{ width: s(12), height: s(12), borderRadius: s(6), backgroundColor: LEVEL[level].dot, marginRight: s(12) }} />
                <Text style={{ color: colors.text, fontSize: s(30), fontWeight: '600', marginRight: s(10) }}>{board.counts[level]}</Text>
                <Text style={{ color: colors.muted, fontSize: s(17) }}>{LEVEL[level].label}</Text>
              </View>
            ))}
            <View style={{ flex: 1 }} />
            {(
              [
                ['Flights today', board.day.flights],
                ['Passengers', board.day.passengers.toLocaleString('en-IN')],
                ['Crew out', board.day.crew_out],
                ['Reserves on call', board.day.reserves_on_call],
              ] as const
            ).map(([name, value], i, all) => (
              <View key={name} style={{ ...tile, marginRight: i === all.length - 1 ? 0 : s(12) }}>
                <Text style={{ color: colors.text, fontSize: s(26), fontWeight: '600' }}>{value}</Text>
                <Text style={{ color: colors.muted, fontSize: s(15) }}>{name}</Text>
              </View>
            ))}
          </View>

          <View style={{ flex: 1, flexDirection: 'row', marginTop: s(24) }}>
            {/* Desks: who holds what. */}
            <View style={{ width: '24%', marginRight: s(28) }}>
              <Text style={{ ...label, marginBottom: s(12) }}>DESKS</Text>
              <TVFocusGuideView>
                <AllDesksButton selected={deskFilter === null} onPress={() => setDeskFilter(null)} />
                {board.desks.map((d) => (
                  <DeskButton
                    key={d.desk_id}
                    desk={d}
                    selected={deskFilter === d.desk_id}
                    onPress={() => setDeskFilter((f) => (f === d.desk_id ? null : d.desk_id))}
                  />
                ))}
              </TVFocusGuideView>

              <Text style={{ ...label, marginTop: s(14), marginBottom: s(10) }}>RESERVE COVER TODAY</Text>
              <View style={{ ...tile, marginRight: 0, paddingVertical: s(8) }}>
                {board.reserve_cover.length === 0 ? (
                  <Text style={{ color: colors.amber, fontSize: s(17) }}>No reserves on call</Text>
                ) : (
                  <>
                    <View style={{ flexDirection: 'row', paddingBottom: s(4) }}>
                      <Text style={{ width: s(110) }} />
                      {RANKS.map(([, short]) => (
                        <Text key={short} style={{ color: colors.faint, fontSize: s(13), width: s(52), textAlign: 'right' }}>
                          {short}
                        </Text>
                      ))}
                    </View>
                    {coverRows(board.reserve_cover).map((row) => (
                      <View key={row.key} style={{ flexDirection: 'row', alignItems: 'center', paddingVertical: s(3) }}>
                        <Text style={{ color: AIRLINE[row.airline]?.color ?? colors.sky, fontSize: s(15), fontWeight: '700', width: s(44) }}>
                          {row.airline}
                        </Text>
                        <Text style={{ color: colors.muted, fontSize: s(15), width: s(66) }}>{row.base}</Text>
                        {RANKS.map(([rank, short]) => (
                          <Text
                            key={short}
                            style={{
                              color: row.counts[rank] ? colors.text : colors.amber,
                              fontSize: s(16),
                              fontWeight: '600',
                              width: s(52),
                              textAlign: 'right',
                            }}>
                            {row.counts[rank] ?? 0}
                          </Text>
                        ))}
                      </View>
                    ))}
                  </>
                )}
              </View>
            </View>

            {/* The disruptions: the most serious first; D-pad to move, OK to open. */}
            <View style={{ flex: 1 }}>
              <View style={{ flexDirection: 'row', alignItems: 'baseline', marginBottom: s(12) }}>
                <Text style={label}>
                  OPEN DISRUPTIONS  ·  {chosen ? `${chosen.controller.toUpperCase()}  ·  ` : ''}
                  {shown.length}
                </Text>
                <Text style={{ color: colors.faint, fontSize: s(15), marginLeft: s(16) }}>Arrows to move · OK to open</Text>
              </View>
              {shown.length === 0 ? (
                <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center' }}>
                  <Text style={{ color: colors.green, fontSize: s(26) }}>
                    {chosen ? `Nothing open on ${chosen.controller}'s desk.` : 'All quiet. No disruption is open.'}
                  </Text>
                </View>
              ) : (
                <ScrollView style={{ flex: 1 }}>
                  <TVFocusGuideView autoFocus style={{ flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'space-between' }}>
                    {shown.map((card, i) => (
                      <DisruptionCard key={card.disruption_id} card={card} first={i === 0 && !open} onPress={() => setOpenId(card.disruption_id)} />
                    ))}
                  </TVFocusGuideView>
                </ScrollView>
              )}

              <Text style={{ ...label, marginTop: s(10), marginBottom: s(10) }}>NEXT DEPARTURES</Text>
              <View style={{ flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'space-between' }}>
                {board.departures.map((f) => (
                  <View
                    key={`${f.flight_no}-${f.dep_utc}`}
                    style={{
                      ...tile,
                      marginRight: 0,
                      width: '24.2%',
                      marginBottom: s(10),
                      paddingVertical: s(8),
                      borderLeftWidth: s(4),
                      borderLeftColor: f.state === 'on plan' ? colors.line : f.state === 'affected' ? colors.amber : colors.red,
                    }}>
                    <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
                      <Text style={{ color: AIRLINE[f.airline ?? '']?.color ?? colors.text, fontSize: s(17), fontWeight: '700' }}>
                        {f.flight_no}
                      </Text>
                      <Text style={{ color: colors.text, fontSize: s(17) }}>{f.dep_utc.slice(11, 16)}Z</Text>
                    </View>
                    <Text style={{ color: colors.muted, fontSize: s(15) }} numberOfLines={1}>
                      {f.from} → {f.to}  ·  {f.state === 'on plan' ? 'on plan' : f.state}
                    </Text>
                  </View>
                ))}
              </View>
            </View>
          </View>

          <View style={{ flexDirection: 'row', alignItems: 'center', marginTop: s(6) }}>
            <Text style={{ color: colors.faint, fontSize: s(14), flex: 1 }}>
              Updated {updatedAt ? `${Math.max(0, Math.round((now - updatedAt) / 1000))} s ago` : '—'}  ·  the same alerts reach
              each desk by Alexa, desktop alerts and the desk speaker
            </Text>
            <View style={{ flexDirection: 'row', alignItems: 'center' }}>
              <View
                style={{
                  width: s(8),
                  height: s(8),
                  borderRadius: s(4),
                  marginRight: s(8),
                  backgroundColor: board.system.models.state === 'closed' ? colors.green : colors.amber,
                }}
              />
              <Text style={{ color: colors.faint, fontSize: s(14) }}>
                {board.system.models.state === 'closed'
                  ? 'AI models online · every answer checked by the engine'
                  : `AI models paused (retry in ${board.system.models.retry_in_seconds} s) · answers from the roster`}
              </Text>
            </View>
          </View>
        </>
      )}

      {open && <DetailPanel card={open} onClose={() => setOpenId(null)} />}
    </View>
  );
};
