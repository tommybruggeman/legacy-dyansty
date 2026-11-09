import * as ImagePicker from 'expo-image-picker';
import { router } from 'expo-router';
import { useEffect, useState } from 'react';
import { ActivityIndicator, Alert, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';

import { Avatar } from '../components/Pictures';
import { ErrorState, Loading } from '../components/ScreenState';
import { Back, Card, Note, Screen, Title } from '../components/ui';
import { type MySettings, type OwnerProfile, apiSend, clearApiCache, useApi } from '../lib/api';
import { supabase } from '../lib/supabase';
import { colors } from '../lib/theme';

const BUCKET = 'owner-pictures';

// The team's name, short name and picture, plus the owner's own name. What the league sees everywhere.
export default function Profile() {
  const { data, error, loading, reload } = useApi<MySettings>('/me/settings');
  const [form, setForm] = useState<OwnerProfile | null>(null);
  const [picture, setPicture] = useState<string | null | undefined>(undefined); // undefined = unchanged
  const [busy, setBusy] = useState<'upload' | 'save' | null>(null);

  useEffect(() => {
    if (data && !form) setForm(data.profile);
  }, [data, form]);

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState message={error} onRetry={reload} />;
  if (!data || !form) return null;

  const shown = picture === undefined ? form.picture_url : picture;
  const set = (patch: Partial<OwnerProfile>) => setForm({ ...form, ...patch });

  const pick = async () => {
    const res = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ['images'], allowsEditing: true, aspect: [1, 1], quality: 0.4, base64: true });
    if (res.canceled || !res.assets?.[0]) return;
    setBusy('upload');
    try {
      const base64 = res.assets[0].base64;
      if (!base64) throw new Error('No picture data.');
      const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
      const path = `${data.team_id}/${Date.now()}.jpg`; // a new name each time, so phones don't show the old one
      const { error: upErr } = await supabase.storage.from(BUCKET).upload(path, bytes, { contentType: 'image/jpeg', upsert: false });
      if (upErr) throw upErr;
      setPicture(supabase.storage.from(BUCKET).getPublicUrl(path).data.publicUrl);
    } catch (e: any) {
      Alert.alert("Picture didn't upload", e?.message ?? 'Try again.');
    } finally {
      setBusy(null);
    }
  };

  const save = async () => {
    setBusy('save');
    try {
      await apiSend('POST', '/me/profile', {
        display_name: form.display_name, team_name: form.team_name, team_abbr: form.team_abbr,
        ...(picture === undefined ? { keep_picture: true } : { keep_picture: false, picture_url: picture }),
      });
      await clearApiCache(); // names and pictures show everywhere, not just here
      router.back();
    } catch (e: any) {
      Alert.alert("Didn't save", e?.message ?? 'Try again.');
    } finally {
      setBusy(null);
    }
  };

  return (
    <Screen>
      <Back label="Settings" />
      <Title>Your team</Title>
      <View style={styles.center}>
        <Pressable onPress={pick} disabled={!!busy}>
          <Avatar uri={shown} name={form.team_name || form.display_name} size={110} me />
          {busy === 'upload' ? <ActivityIndicator style={StyleSheet.absoluteFill} color={colors.gold} /> : null}
        </Pressable>
        <Pressable onPress={pick} disabled={!!busy} hitSlop={8}><Text style={styles.link}>Change picture</Text></Pressable>
        {shown ? <Pressable onPress={() => setPicture(null)} hitSlop={8}><Text style={styles.muted}>Remove picture</Text></Pressable> : null}
      </View>
      <Card>
        <Field label="Team name" value={form.team_name} onChange={(v) => set({ team_name: v })} max={40} />
        <Field label="Short name (2-4 letters)" value={form.team_abbr} max={4} caps
               onChange={(v) => set({ team_abbr: v.toUpperCase().replace(/[^A-Z0-9]/g, '') })} />
        <Field label="Your name" value={form.display_name} onChange={(v) => set({ display_name: v })} max={40} />
        <Note>The short name shows where space is tight, like scoreboards.</Note>
      </Card>
      <Pressable style={[styles.save, !!busy && styles.off]} disabled={!!busy} onPress={save}>
        {busy === 'save' ? <ActivityIndicator color={colors.bg} /> : <Text style={styles.saveText}>Save</Text>}
      </Pressable>
    </Screen>
  );
}

function Field({ label, value, onChange, max, caps }: { label: string; value: string; onChange: (v: string) => void; max: number; caps?: boolean }) {
  return (
    <View style={styles.field}>
      <Text style={styles.label}>{label}</Text>
      <TextInput
        style={styles.input} value={value} onChangeText={onChange} maxLength={max}
        autoCapitalize={caps ? 'characters' : 'words'} autoCorrect={false} placeholderTextColor={colors.muted}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  center: { alignItems: 'center', gap: 10, marginBottom: 16 },
  link: { color: colors.gold, fontWeight: '700', fontSize: 14 },
  muted: { color: colors.muted, fontSize: 13 },
  field: { marginBottom: 12 },
  label: { color: colors.muted, fontSize: 12, fontWeight: '700', marginBottom: 6 },
  input: { backgroundColor: colors.card, color: colors.text, borderRadius: 10, paddingHorizontal: 12, paddingVertical: 10, fontSize: 16 },
  save: { backgroundColor: colors.gold, borderRadius: 12, paddingVertical: 14, alignItems: 'center' },
  saveText: { color: colors.bg, fontWeight: '800', fontSize: 16 },
  off: { opacity: 0.5 },
});
