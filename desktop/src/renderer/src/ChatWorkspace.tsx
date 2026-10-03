/** Renders the bounded, transient, privacy-explicit desktop chat workspace. */
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import type {
  AncestryBridge,
  BridgeErrorCode,
  ChatCapability,
  ChatEventDelivery,
  ChatPurpose,
  ChatSession,
  ConsentGrantSummary,
  ProviderConfiguration,
  ProviderDataClass,
  ProviderProfileSummary,
} from '../../shared-contract/desktop'
import { chatPurposes, providerDataClasses } from '../../shared-contract/desktop'
import { Button } from './components/Button'
import { CodedErrorView } from './design-system/CodedErrorView'
import { SafeMarkdown } from './SafeMarkdown'
import {
  applyChatDelivery,
  createChatResponseState,
  type ChatResponseState,
} from './chat-state'

/** Maximum retained chat turns; older completed responses are removed from the rendered transcript. */
export const MAX_VISIBLE_CHAT_TURNS = 24
const MAX_PENDING_EVENT_RUNS = 32
const MAX_PENDING_DELIVERIES_PER_RUN = 8

const providerLabels = Object.freeze({
  ollama: 'Ollama',
  openai: 'OpenAI',
  anthropic: 'Anthropic',
  gemini: 'Gemini',
  openrouter: 'OpenRouter',
} as const)

const purposeLabels: Readonly<Record<ChatPurpose, string>> = Object.freeze({
  genealogy_analysis: 'Genealogy analysis',
  source_analysis: 'Source analysis',
  writing_assistance: 'Writing assistance',
})

const dataClassLabels: Readonly<Record<ProviderDataClass, string>> = Object.freeze({
  public_genealogy: 'Public genealogy',
  deceased_person: 'Deceased person',
  living_person: 'Living person',
  possibly_living_person: 'Possibly living person',
  free_text_note: 'Free-text note',
  source_transcription: 'Source transcription',
  government_identifier: 'Government identifier',
})

type ChatTurn = Readonly<{
  id: string
  role: 'user' | 'assistant'
  text: string
  response: Readonly<ChatResponseState> | null
}>

type ChatConversation = Readonly<{
  id: string
  title: string
  session: Readonly<ChatSession> | null
  turns: readonly ChatTurn[]
  lastUserMessage: string | null
}>

type ResponsePointer = Readonly<{
  conversationId: string
  turnId: string
  state: Readonly<ChatResponseState>
}>

interface ChatWorkspaceProps {
  readonly bridge?: AncestryBridge
}

function newConversation(id: string): ChatConversation {
  return Object.freeze({
    id,
    title: 'New conversation',
    session: null,
    turns: Object.freeze([]),
    lastUserMessage: null,
  })
}

function titleFromMessage(message: string): string {
  const singleLine = message.replace(/\s+/g, ' ').trim()
  const characters = Array.from(singleLine)
  return characters.length <= 42 ? singleLine : `${characters.slice(0, 39).join('')}…`
}

function responseIsActive(response: Readonly<ChatResponseState>): boolean {
  return response.status === 'starting'
    || response.status === 'streaming'
    || response.status === 'cancelling'
}

function consentSupports(
  consent: Readonly<ConsentGrantSummary>,
  profile: Readonly<ProviderProfileSummary>,
  purpose: ChatPurpose,
  dataClass: ProviderDataClass,
): boolean {
  return consent.active
    && consent.provider_profile_name === profile.name
    && consent.provider_id === profile.provider_id
    && consent.modules.includes('chat')
    && consent.purposes.includes(purpose)
    && consent.data_classes.includes(dataClass)
    && consent.models.includes(profile.model)
}

function statusAnnouncement(status: Readonly<ChatResponseState>['status']): string {
  if (status === 'completed') return 'Response completed'
  if (status === 'interrupted') return 'Response interrupted'
  if (status === 'failed') return 'Response failed'
  if (status === 'cancelling') return 'Stopping response'
  return 'Response streaming'
}

const windowBridge = (): AncestryBridge => (
  window as unknown as { ancestry: AncestryBridge }
).ancestry

/**
 * Accessible, transient chat UI. The renderer owns display state only: every
 * provider, clipboard, link, session, and streaming action crosses the narrow
 * preload bridge and is re-authorized by the main process.
 */
export function ChatWorkspace({ bridge: providedBridge }: ChatWorkspaceProps) {
  const bridge = providedBridge ?? windowBridge()
  const [configuration, setConfiguration] = useState<Readonly<ProviderConfiguration> | null>(null)
  const [capability, setCapability] = useState<Readonly<ChatCapability> | null>(null)
  const [loading, setLoading] = useState(true)
  const [failure, setFailure] = useState<BridgeErrorCode | null>(null)
  const [announcement, setAnnouncement] = useState('Chat is loading.')
  const [selectedProfileName, setSelectedProfileName] = useState('')
  const [selectedPurpose, setSelectedPurpose] = useState<ChatPurpose>('genealogy_analysis')
  const [selectedDataClass, setSelectedDataClass] = useState<ProviderDataClass>('public_genealogy')
  const [selectedConsentName, setSelectedConsentName] = useState('')
  const [message, setMessage] = useState('')
  const [pendingConversationId, setPendingConversationId] = useState<string | null>(null)
  const [conversations, setConversations] = useState<readonly ChatConversation[]>(() => [
    newConversation('conversation-1'),
  ])
  const [activeConversationId, setActiveConversationId] = useState('conversation-1')
  const conversationsRef = useRef(conversations)
  const sessionsRef = useRef(new Map<string, Readonly<ChatSession>>())
  const responsesRef = useRef(new Map<string, ResponsePointer>())
  const pendingDeliveriesRef = useRef(new Map<string, readonly Readonly<ChatEventDelivery>[]>() )
  const nextConversationId = useRef(2)
  const nextTurnId = useRef(1)

  const replaceConversations = useCallback((
    update: (current: readonly ChatConversation[]) => readonly ChatConversation[],
  ) => {
    setConversations((current) => {
      const next = update(current)
      conversationsRef.current = next
      return next
    })
  }, [])

  const replaceResponse = useCallback((
    runId: string,
    state: Readonly<ChatResponseState>,
  ) => {
    const pointer = responsesRef.current.get(runId)
    if (pointer === undefined) return
    responsesRef.current.set(runId, Object.freeze({ ...pointer, state }))
    replaceConversations((current) => current.map((conversation) => (
      conversation.id !== pointer.conversationId
        ? conversation
        : Object.freeze({
          ...conversation,
          turns: conversation.turns.map((turn) => turn.id === pointer.turnId
            ? Object.freeze({ ...turn, response: state, text: state.text })
            : turn),
        })
    )))
  }, [replaceConversations])

  const handleDelivery = useCallback((
    delivery: Readonly<ChatEventDelivery>,
    bufferUnknown = true,
  ) => {
    const pointer = responsesRef.current.get(delivery.run_id)
    if (pointer === undefined) {
      if (!bufferUnknown) return
      const pending = pendingDeliveriesRef.current
      if (!pending.has(delivery.run_id) && pending.size >= MAX_PENDING_EVENT_RUNS) {
        setFailure('CHAT_STREAM_EVENT_INVALID')
        setAnnouncement('A response event was rejected')
        return
      }
      const deliveries = pending.get(delivery.run_id) ?? []
      if (deliveries.length >= MAX_PENDING_DELIVERIES_PER_RUN) {
        setFailure('CHAT_STREAM_EVENT_INVALID')
        setAnnouncement('A response event was rejected')
        return
      }
      pending.set(delivery.run_id, Object.freeze([...deliveries, delivery]))
      return
    }

    const applied = applyChatDelivery(pointer.state, delivery)
    replaceResponse(delivery.run_id, applied.state)
    setAnnouncement(statusAnnouncement(applied.state.status))
    if (applied.acknowledgeThrough === null) return
    void bridge.acknowledgeChatStream({
      schema_version: 1,
      session_id: applied.state.sessionId,
      run_id: applied.state.runId,
      through_sequence: applied.acknowledgeThrough,
    }).then((result) => {
      if (result.ok) return
      const current = responsesRef.current.get(delivery.run_id)
      if (current === undefined || !responseIsActive(current.state)) return
      const interrupted = Object.freeze({
        ...current.state,
        status: 'interrupted' as const,
        failureCode: result.error.code,
      })
      replaceResponse(delivery.run_id, interrupted)
      setAnnouncement('Response interrupted')
    }).catch(() => {
      const current = responsesRef.current.get(delivery.run_id)
      if (current === undefined || !responseIsActive(current.state)) return
      const interrupted = Object.freeze({
        ...current.state,
        status: 'interrupted' as const,
        failureCode: 'CHAT_STREAM_SERVICE_UNAVAILABLE',
      })
      replaceResponse(delivery.run_id, interrupted)
      setAnnouncement('Response interrupted')
    })
  }, [bridge, replaceResponse])

  useEffect(() => bridge.onChatEventBatch((delivery) => handleDelivery(delivery)), [bridge, handleDelivery])

  useEffect(() => {
    let active = true
    setLoading(true)
    void Promise.all([bridge.getProviderConfiguration(), bridge.getChatCapability()])
      .then(([configurationResult, capabilityResult]) => {
        if (!active) return
        if (!configurationResult.ok) {
          setFailure(configurationResult.error.code)
          return
        }
        if (!capabilityResult.ok) {
          setFailure(capabilityResult.error.code)
          return
        }
        const enabledProfiles = configurationResult.data.profiles.filter((profile) => profile.enabled)
        setConfiguration(configurationResult.data)
        setCapability(capabilityResult.data)
        setSelectedProfileName(enabledProfiles[0]?.name ?? '')
        setAnnouncement(enabledProfiles.length > 0
          ? 'Chat is ready.'
          : 'Configure an enabled provider before starting chat.')
      })
      .catch(() => {
        if (!active) return
        setFailure('CHAT_SESSION_SERVICE_UNAVAILABLE')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => { active = false }
  }, [bridge])

  useEffect(() => () => {
    const sessions = [...sessionsRef.current.values()]
    sessionsRef.current.clear()
    responsesRef.current.clear()
    pendingDeliveriesRef.current.clear()
    for (const session of sessions) {
      void bridge.closeChatSession({ schema_version: 1, session_id: session.session_id })
    }
  }, [bridge])

  const enabledProfiles = useMemo(() => (
    configuration?.profiles.filter((profile) => profile.enabled) ?? []
  ), [configuration])
  const activeConversation = conversations.find((conversation) => conversation.id === activeConversationId)
    ?? conversations[0]!
  const selectedProfile = enabledProfiles.find((profile) => profile.name === (
    activeConversation.session?.provider_profile_name ?? selectedProfileName
  )) ?? null
  const compatibleConsents = useMemo(() => {
    if (configuration === null || selectedProfile === null) return []
    return configuration.consents.filter((consent) => consentSupports(
      consent,
      selectedProfile,
      selectedPurpose,
      selectedDataClass,
    ))
  }, [configuration, selectedDataClass, selectedProfile, selectedPurpose])

  useEffect(() => {
    if (activeConversation.session !== null) return
    if (selectedProfile?.endpoint_kind !== 'remote') {
      if (selectedConsentName !== '') setSelectedConsentName('')
      return
    }
    if (!compatibleConsents.some((consent) => consent.name === selectedConsentName)) {
      setSelectedConsentName(compatibleConsents[0]?.name ?? '')
    }
  }, [activeConversation.session, compatibleConsents, selectedConsentName, selectedProfile])

  const selectedConsent = compatibleConsents.find((consent) => consent.name === (
    activeConversation.session?.consent_name ?? selectedConsentName
  )) ?? (activeConversation.session === null ? compatibleConsents[0] ?? null : null)
  const controlsLocked = activeConversation.session !== null || pendingConversationId !== null
  const activeResponse = [...activeConversation.turns].reverse().find((turn) => (
    turn.response !== null && responseIsActive(turn.response)
  ))?.response ?? null
  const canSend = !loading
    && capability !== null
    && selectedProfile !== null
    && pendingConversationId === null
    && activeResponse === null
    && message.trim().length > 0
    && message.length <= capability.max_message_characters
    && (selectedProfile.endpoint_kind === 'loopback' || selectedConsent !== null)

  const addResponse = useCallback((
    conversationId: string,
    session: Readonly<ChatSession>,
    sourceMessage: string,
    runId: string,
    includeUser: boolean,
  ) => {
    const userTurnId = `turn-${nextTurnId.current++}`
    const responseTurnId = `turn-${nextTurnId.current++}`
    const response = createChatResponseState(session.session_id, runId)
    responsesRef.current.set(runId, Object.freeze({
      conversationId,
      turnId: responseTurnId,
      state: response,
    }))
    replaceConversations((current) => current.map((conversation) => {
      if (conversation.id !== conversationId) return conversation
      const appended: readonly ChatTurn[] = [
        ...(includeUser ? [Object.freeze({
          id: userTurnId,
          role: 'user' as const,
          text: sourceMessage,
          response: null,
        })] : []),
        Object.freeze({
          id: responseTurnId,
          role: 'assistant' as const,
          text: '',
          response,
        }),
      ]
      return Object.freeze({
        ...conversation,
        title: conversation.lastUserMessage === null ? titleFromMessage(sourceMessage) : conversation.title,
        session,
        turns: Object.freeze([...conversation.turns, ...appended]),
        lastUserMessage: sourceMessage,
      })
    }))
    const pending = pendingDeliveriesRef.current.get(runId) ?? []
    pendingDeliveriesRef.current.delete(runId)
    for (const delivery of pending) handleDelivery(delivery, false)
  }, [handleDelivery, replaceConversations])

  const startResponse = async (sourceMessage: string, includeUser: boolean) => {
    const conversation = conversationsRef.current.find((item) => item.id === activeConversationId)
    if (conversation === undefined || capability === null || selectedProfile === null
      || pendingConversationId !== null || activeResponse !== null) return
    const trimmedMessage = sourceMessage.trim()
    if (trimmedMessage.length < 1 || Array.from(trimmedMessage).length > capability.max_message_characters) return
    if (selectedProfile.endpoint_kind === 'remote' && selectedConsent === null) {
      setFailure('CONSENT_INVALID')
      setAnnouncement('A compatible consent is required')
      return
    }

    setPendingConversationId(conversation.id)
    setFailure(null)
    try {
      let session = conversation.session
      if (session === null) {
        const created = await bridge.createChatSession({
          schema_version: 1,
          provider_profile_name: selectedProfile.name,
          model: selectedProfile.model,
          purpose: selectedPurpose,
          data_classes: [selectedDataClass],
          consent_name: selectedProfile.endpoint_kind === 'remote' ? selectedConsent!.name : null,
        })
        if (!created.ok) {
          setFailure(created.error.code)
          setAnnouncement('Chat session could not be created')
          return
        }
        session = created.data
        sessionsRef.current.set(conversation.id, session)
        replaceConversations((current) => current.map((item) => item.id === conversation.id
          ? Object.freeze({ ...item, session })
          : item))
      }
      const started = await bridge.startChatStream({
        schema_version: 1,
        session_id: session.session_id,
        message: trimmedMessage,
        max_output_tokens: capability.max_output_tokens,
        temperature: 0,
        timeout_seconds: capability.max_timeout_seconds,
        max_safe_retries: capability.max_safe_retries,
      })
      if (!started.ok) {
        setFailure(started.error.code)
        setAnnouncement('Response could not be started')
        return
      }
      addResponse(conversation.id, session, trimmedMessage, started.data.run_id, includeUser)
      if (includeUser) setMessage('')
      setAnnouncement('Response started')
    } catch {
      setFailure('CHAT_STREAM_SERVICE_UNAVAILABLE')
      setAnnouncement('Response could not be started')
    } finally {
      setPendingConversationId(null)
    }
  }

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (canSend) void startResponse(message, true)
  }

  const submitFromKeyboard = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== 'Enter' || (!event.ctrlKey && !event.metaKey)) return
    event.preventDefault()
    if (canSend) void startResponse(message, true)
  }

  const stopResponse = async () => {
    if (activeResponse === null || !responseIsActive(activeResponse)) return
    try {
      const result = await bridge.cancelChatStream({
        schema_version: 1,
        session_id: activeResponse.sessionId,
        run_id: activeResponse.runId,
      })
      if (!result.ok) {
        setFailure(result.error.code)
        return
      }
      const current = responsesRef.current.get(activeResponse.runId)
      if (current !== undefined && responseIsActive(current.state)) {
        replaceResponse(activeResponse.runId, Object.freeze({
          ...current.state,
          status: 'cancelling',
        }))
      }
      setAnnouncement('Stopping response')
    } catch {
      setFailure('CHAT_STREAM_SERVICE_UNAVAILABLE')
    }
  }

  const openExternal = async (destination: string) => {
    try {
      const result = await bridge.openExternalLink({ schema_version: 1, destination })
      if (!result.ok) {
        setFailure(result.error.code)
        setAnnouncement('External link was not opened')
        return
      }
      setAnnouncement(result.data.status === 'opened'
        ? 'External link opened'
        : 'External link cancelled')
    } catch {
      setFailure('INTERNAL_ERROR')
      setAnnouncement('External link was not opened')
    }
  }

  const copyResponse = async (text: string) => {
    try {
      const result = await bridge.copyText({ schema_version: 1, text })
      if (!result.ok) {
        setFailure(result.error.code)
        setAnnouncement('Response was not copied')
        return
      }
      setAnnouncement('Response copied')
    } catch {
      setFailure('INTERNAL_ERROR')
      setAnnouncement('Response was not copied')
    }
  }

  const selectConversation = (conversation: Readonly<ChatConversation>) => {
    setActiveConversationId(conversation.id)
    if (conversation.session !== null) {
      setSelectedProfileName(conversation.session.provider_profile_name)
      setSelectedPurpose(conversation.session.purpose)
      setSelectedDataClass(conversation.session.data_classes[0] ?? 'public_genealogy')
      setSelectedConsentName(conversation.session.consent_name ?? '')
    }
    setFailure(null)
    setAnnouncement(`Opened ${conversation.title}`)
  }

  const createConversation = () => {
    const maximum = capability?.max_active_sessions ?? 32
    if (conversationsRef.current.length >= maximum) {
      setFailure('CHAT_SESSION_LIMIT')
      return
    }
    const conversation = newConversation(`conversation-${nextConversationId.current++}`)
    replaceConversations((current) => Object.freeze([...current, conversation]))
    setActiveConversationId(conversation.id)
    setFailure(null)
    setAnnouncement('New conversation ready')
  }

  const closeConversation = async () => {
    if (activeResponse !== null || conversationsRef.current.length === 1) return
    const conversation = activeConversation
    if (conversation.session !== null) {
      try {
        const result = await bridge.closeChatSession({
          schema_version: 1,
          session_id: conversation.session.session_id,
        })
        if (!result.ok) {
          setFailure(result.error.code)
          return
        }
      } catch {
        setFailure('CHAT_SESSION_SERVICE_UNAVAILABLE')
        return
      }
      sessionsRef.current.delete(conversation.id)
    }
    for (const turn of conversation.turns) {
      if (turn.response !== null) responsesRef.current.delete(turn.response.runId)
    }
    const remaining = conversationsRef.current.filter((item) => item.id !== conversation.id)
    replaceConversations(() => Object.freeze(remaining))
    setActiveConversationId(remaining[0]!.id)
    setAnnouncement('Conversation closed')
  }

  const visibleTurns = activeConversation.turns.slice(-MAX_VISIBLE_CHAT_TURNS)
  const hiddenTurnCount = activeConversation.turns.length - visibleTurns.length
  const lastAssistantTurn = [...activeConversation.turns].reverse().find((turn) => turn.response !== null) ?? null
  const canRegenerate = lastAssistantTurn !== null
    && lastAssistantTurn.response !== null
    && !responseIsActive(lastAssistantTurn.response)
    && activeConversation.lastUserMessage !== null
    && pendingConversationId === null

  let privacyText = 'Choose an enabled provider before composing a message.'
  if (selectedProfile?.endpoint_kind === 'loopback') {
    privacyText = `${selectedProfile.name} stays on this device at ${selectedProfile.endpoint}. `
      + 'Payload retention is disabled. Chat is transient, and model output is not evidence.'
  } else if (selectedProfile !== null) {
    const consentText = selectedConsent === null
      ? 'No compatible active consent is selected.'
      : `Consent: ${selectedConsent.name}. Provider retention is ${selectedConsent.retain_payloads ? '' : 'not '}permitted.`
    privacyText = `This message leaves this device for ${providerLabels[selectedProfile.provider_id]} at ${selectedProfile.endpoint}. `
      + `${consentText} AncestryLLM payload retention is disabled. Chat is transient, and model output is not evidence.`
  }

  return <div className="chat-workspace">
    <p className="sr-only" role="status" aria-live="polite" aria-label="Chat activity">
      {announcement}
    </p>
    {failure !== null && <CodedErrorView
      code={failure}
      title="Chat needs attention."
      recovery="Review the selected provider and consent, then try again."
    />}
    {loading && <p role="status">Loading private chat…</p>}
    {!loading && enabledProfiles.length === 0 && <section className="chat-empty" aria-labelledby="chat-empty-title">
      <h2 id="chat-empty-title">Configure a provider to start</h2>
      <p>Chat stays disabled until an enabled local or reviewed remote provider profile is available.</p>
      <a href="#/settings">Open provider settings</a>
    </section>}
    {!loading && enabledProfiles.length > 0 && <div className="chat-layout">
      <aside className="chat-session-rail" aria-label="Chat sessions">
        <Button type="button" onClick={createConversation}>New conversation</Button>
        <ol>
          {conversations.map((conversation) => <li key={conversation.id}>
            <button
              type="button"
              className="chat-session-button"
              aria-current={conversation.id === activeConversation.id ? 'true' : undefined}
              onClick={() => selectConversation(conversation)}
            >
              <span>{conversation.title}</span>
              <small>{conversation.session?.remote ? 'Remote' : conversation.session ? 'Local' : 'Not started'}</small>
            </button>
          </li>)}
        </ol>
        <Button
          type="button"
          variant="quiet"
          disabled={conversations.length === 1 || activeResponse !== null}
          onClick={() => { void closeConversation() }}
        >
          Close conversation
        </Button>
      </aside>

      <section className="chat-main" aria-labelledby="chat-conversation-title">
        <header className="chat-main-header">
          <div>
            <p className="eyebrow">Transient conversation</p>
            <h2 id="chat-conversation-title">{activeConversation.title}</h2>
          </div>
          <p><span className="badge">Not saved</span></p>
        </header>

        <fieldset className="chat-provider-controls" disabled={controlsLocked}>
          <legend>Provider and privacy scope</legend>
          <label>
            Provider
            <select
              aria-label="Provider"
              value={selectedProfile?.name ?? ''}
              onChange={(event) => {
                setSelectedProfileName(event.currentTarget.value)
                setSelectedConsentName('')
              }}
            >
              {enabledProfiles.map((profile) => <option key={profile.name} value={profile.name}>{profile.name}</option>)}
            </select>
          </label>
          <label>
            Model
            <select aria-label="Model" value={selectedProfile?.model ?? ''} disabled>
              {selectedProfile !== null && <option value={selectedProfile.model}>{selectedProfile.model}</option>}
            </select>
          </label>
          <label>
            Purpose
            <select
              aria-label="Purpose"
              value={selectedPurpose}
              onChange={(event) => setSelectedPurpose(event.currentTarget.value as ChatPurpose)}
            >
              {chatPurposes.map((purpose) => <option key={purpose} value={purpose}>{purposeLabels[purpose]}</option>)}
            </select>
          </label>
          <label>
            Data shared
            <select
              aria-label="Data shared"
              value={selectedDataClass}
              onChange={(event) => setSelectedDataClass(event.currentTarget.value as ProviderDataClass)}
            >
              {providerDataClasses.map((dataClass) => <option key={dataClass} value={dataClass}>{dataClassLabels[dataClass]}</option>)}
            </select>
          </label>
          <label>
            Consent
            <select
              aria-label="Consent"
              value={selectedProfile?.endpoint_kind === 'remote' ? selectedConsent?.name ?? '' : ''}
              onChange={(event) => setSelectedConsentName(event.currentTarget.value)}
              disabled={controlsLocked || selectedProfile?.endpoint_kind !== 'remote'}
            >
              {selectedProfile?.endpoint_kind === 'remote'
                ? <>
                  {compatibleConsents.length === 0 && <option value="">No compatible consent</option>}
                  {compatibleConsents.map((consent) => <option key={consent.name} value={consent.name}>{consent.name}</option>)}
                </>
                : <option value="">Not required for local provider</option>}
            </select>
          </label>
        </fieldset>

        <div className="chat-transcript" role="log" aria-label="Conversation transcript" aria-live="off">
          {hiddenTurnCount > 0 && <p className="chat-window-notice">
            {hiddenTurnCount} earlier {hiddenTurnCount === 1 ? 'message is' : 'messages are'} outside the rendered window.
          </p>}
          {visibleTurns.length === 0 && <div className="chat-transcript-empty">
            <p>Ask about fictional genealogy evidence, sources, or writing.</p>
            <p>Provider output can be wrong. Verify it against the underlying records.</p>
          </div>}
          {visibleTurns.map((turn) => turn.role === 'user'
            ? <article className="chat-turn chat-turn--user" aria-label="Your message" key={turn.id}>
              <p className="chat-turn-label">You</p>
              <p className="chat-user-text">{turn.text}</p>
            </article>
            : <article className="chat-turn chat-turn--assistant" aria-label="Assistant response" key={turn.id}>
              <p className="chat-turn-label">Assistant</p>
              {turn.response?.text
                ? <SafeMarkdown content={turn.response.text} onOpenExternal={openExternal} />
                : <p>{turn.response?.status === 'starting' ? 'Starting response…' : 'No response text was returned.'}</p>}
              {turn.response !== null && <>
                <p className="chat-response-state">Response status: {turn.response.status}</p>
                {turn.response.failureCode !== null && <p className="error-code">Code: {turn.response.failureCode}</p>}
                <p className="chat-usage">Tokens: unavailable · Cost: unavailable</p>
                <p className="chat-response-identity">
                  {turn.response.providerId && turn.response.model
                    ? `${providerLabels[turn.response.providerId as keyof typeof providerLabels] ?? turn.response.providerId} · ${turn.response.model}`
                    : 'Provider identity pending'}
                </p>
                {turn.response.text.length > 0 && <Button
                  type="button"
                  variant="quiet"
                  aria-label="Copy response as plain text"
                  onClick={() => { void copyResponse(turn.response!.text) }}
                >
                  Copy response
                </Button>}
              </>}
            </article>)}
        </div>

        <form className="chat-composer" onSubmit={submit}>
          <div className="chat-privacy-note" role="note" aria-label="Chat privacy and retention">
            <strong>Privacy and retention</strong>
            <p>{privacyText}</p>
          </div>
          <label htmlFor="chat-message">Message</label>
          <textarea
            id="chat-message"
            aria-label="Message"
            value={message}
            rows={4}
            maxLength={capability?.max_message_characters ?? 16_384}
            disabled={pendingConversationId !== null || activeResponse !== null}
            onChange={(event) => setMessage(event.currentTarget.value)}
            onKeyDown={submitFromKeyboard}
          />
          <div className="chat-composer-meta">
            <span>{Array.from(message).length.toLocaleString()} / {(capability?.max_message_characters ?? 16_384).toLocaleString()} characters</span>
            <span><kbd>Ctrl</kbd> + <kbd>Enter</kbd> to send</span>
          </div>
          <div className="chat-composer-actions">
            <Button type="submit" disabled={!canSend} aria-label="Send message">
              {pendingConversationId === activeConversation.id ? 'Starting…' : 'Send message'}
            </Button>
            {activeResponse !== null && <Button
              type="button"
              variant="quiet"
              aria-label="Stop response"
              disabled={activeResponse.status === 'cancelling'}
              onClick={() => { void stopResponse() }}
            >
              Stop response
            </Button>}
            {canRegenerate && <Button
              type="button"
              variant="quiet"
              aria-label="Regenerate last response"
              onClick={() => { void startResponse(activeConversation.lastUserMessage!, false) }}
            >
              Regenerate response
            </Button>}
          </div>
        </form>
      </section>
    </div>}
  </div>
}
