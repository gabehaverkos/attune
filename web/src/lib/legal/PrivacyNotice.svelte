<script lang="ts">
	import type { LegalDocument } from './terms';

	let { show }: { show: (document: LegalDocument) => void } = $props();
</script>

<!-- The Privacy Notice, as shown in the legal dialog. -->

<p>
	This notice says what Attune keeps about you, who else sees it, and how long it stays. It goes
	with the <button type="button" onclick={() => show('terms')}>Terms of Use</button>.
</p>

<h2>What Attune keeps</h2>
<ul>
	<li>
		<strong>Your account:</strong> your email address, the name Google gives if you sign in with it, a
		hash of your password (never the password itself), and when you agreed to these terms.
	</li>
	<li>
		<strong>What you write and what the assistant replies</strong>, and what Attune learns from it:
		its memory of you, which you can see, correct, or remove in the Memory panel.
	</li>
	<li>
		<strong>What you add to interfaces</strong>, such as calendar routines and events, and which
		interfaces you have turned on.
	</li>
	<li>
		<strong>Technical records:</strong> your network address and browser, kept with your sign-in session
		and used to apply daily limits; and logs of each reply, including its content, used to debug and improve
		the assistant.
	</li>
</ul>

<h2>Who else sees it</h2>
<p>Attune sends data to these services, only for the purpose given:</p>
<ul>
	<li>
		<strong>OpenRouter</strong>, which passes your messages and the context needed to answer them to
		the AI model provider that writes the reply. Each handles them under its own terms.
	</li>
	<li>
		<strong>Voyage AI</strong>, which turns what you share into the search index for memory.
	</li>
	<li>
		<strong>Resend</strong>, which sends the emails that verify your address and reset your
		password.
	</li>
	<li>
		<strong>Langfuse</strong>, which records how each reply was produced: its timing, cost, and the
		steps taken, but not what you wrote or what the assistant said.
	</li>
	<li><strong>Google</strong>, if you choose to sign in with Google.</li>
</ul>
<p>
	Attune does not sell your data, show ads, or share it with anyone else, except where the law
	requires.
</p>

<h2>Cookies</h2>
<p>
	Attune uses one cookie, to keep you signed in, and stores small preferences in your browser. There
	are no advertising or tracking cookies.
</p>

<h2>How long it stays</h2>
<ul>
	<li>
		A demo you don't keep is deleted, with everything in it, when you log out of it or after a week
		unused.
	</li>
	<li>
		Deleting a conversation erases its messages, and takes out of memory what it taught, unless
		another conversation also established it.
	</li>
	<li>
		A kept account stays until you delete it, or Attune shuts down. To delete your account and
		everything in it, use Delete your account in the account menu.
	</li>
	<li>
		One thing outlasts a deleted account or demo, for a day: the network address it was used from
		and how much of the daily limit it had used, so that starting again doesn't reset the limit.
	</li>
</ul>

<h2>Your choices</h2>
<p>
	You can see and correct what Attune remembers about you at any time, delete any conversation,
	delete your account, and ask for a copy of your data. Please don't share sensitive information,
	such as health, financial, or government ID details, or other people's personal information.
</p>
