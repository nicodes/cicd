from pathlib import Path
import subprocess
import unittest


class SyntheticIdentity(unittest.TestCase):
    def test_human_credentials_and_identity_mismatches_are_rejected(self):
        source = (Path(__file__).resolve().parents[1]/'helpers/production-auth.mjs').as_uri()
        script = '''
import assert from 'node:assert/strict';
const { verifySyntheticUser } = await import(process.argv[1]);
const user = { id:'user_fixture',object:'user',external_id:'cazper-engineering-verifier',
 private_metadata:{purpose:'engineering_verification',project:'cazper'},
 password_enabled:false,two_factor_enabled:false,totp_enabled:false,backup_code_enabled:false,banned:false,locked:false,
 primary_email_address_id:'idn_fixture',email_addresses:[{id:'idn_fixture',email_address:'cazper-engineering-'+ 'a'.repeat(24)+'@example.com',reserved:true}]};
verifySyntheticUser(user,'cazper');
for(const change of [{password_enabled:true},{external_id:'human'},{private_metadata:{}},{passkeys:[{}]},
 {email_addresses:[{...user.email_addresses[0],reserved:false}]}]) {
 assert.throws(()=>verifySyntheticUser({...user,...change},'cazper'));
}
'''
        subprocess.run(['node', '--input-type=module', '-e', script, source], check=True, timeout=30)


if __name__ == '__main__':
    unittest.main()
