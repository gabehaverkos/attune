<script lang="ts">
	import type { LegalDocument } from './terms';

	let { show }: { show: (document: LegalDocument) => void } = $props();
</script>

<!-- The Terms of Use, as shown in the legal dialog. -->

<p>
	These terms cover your use of Attune, a personal assistant built and run by Gabe Haverkos as a
	portfolio demonstration. By opening a demo or an account, you agree to them and to the
	<button type="button" onclick={() => show('privacy')}>Privacy Notice</button>.
</p>

<h2>A demonstration</h2>
<p>
	Attune is offered free, as is, and as available. It may change, lose data, be unavailable, or stop
	altogether, with or without notice. Don't rely on it for anything important, and keep your own
	copy of anything you need.
</p>

<h2>What the assistant says</h2>
<p>
	Replies are written by AI models and can be wrong, incomplete, or out of date. They are not
	professional advice of any kind: medical, legal, financial, or otherwise. Check anything that
	matters before you act on it. Attune changes your calendar or settings only when you approve a
	proposal for it.
</p>

<h2>Your account</h2>
<ul>
	<li>You must be at least 18 to use Attune.</li>
	<li>
		A demo account needs no email. You can keep it by adding an email or Google sign-in; a demo you
		don't keep is deleted after a week unused.
	</li>
	<li>
		You are responsible for what happens in your account. Keep your password to yourself, and give a
		real email address you can verify.
	</li>
</ul>

<h2>Acceptable use</h2>
<p>Don't use Attune to:</p>
<ul>
	<li>break the law, or harm, harass, or deceive anyone;</li>
	<li>
		enter other people's personal information without their permission, or sensitive information of
		your own, such as health, financial, or government ID details;
	</li>
	<li>
		try to get around its daily limits or approvals, reach other people's data, or attack, overload,
		or reverse-engineer the service;
	</li>
	<li>create accounts automatically or in bulk, or resell access.</li>
</ul>
<p>
	Use is limited each day for every visitor. Accounts that break these terms may be limited or
	deleted.
</p>

<h2>Your content</h2>
<p>
	What you write stays yours. You let Attune store and process it, and send it to the services named
	in the Privacy Notice, only to run the service for you: to answer you, remember what you share,
	and keep it working.
</p>

<h2>Liability</h2>
<p>
	To the fullest extent the law allows, Attune and Gabe Haverkos disclaim all warranties, express or
	implied, and are not liable for any indirect, incidental, or consequential loss, or for any loss
	of data, arising from your use of Attune. Nothing in these terms limits rights you have that the
	law does not allow to be limited.
</p>

<h2>Changes and ending</h2>
<p>
	These terms may change; the date above shows when they last did, and continuing to use Attune
	after a change means you accept it. You can stop using Attune at any time, and access may be ended
	for anyone at any time.
</p>
