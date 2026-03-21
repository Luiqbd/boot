package com.example.loginapp

import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.example.loginapp.databinding.ActivityLoginBinding

class LoginActivity : AppCompatActivity() {

    private lateinit var binding: ActivityLoginBinding

    // Usuários válidos (em um app real, isso viria de um servidor/base de dados)
    private val validUsers = mapOf(
        "admin" to "123456",
        "usuario" to "senha123",
        "teste" to "teste123"
    )

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityLoginBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.btnLogin.setOnClickListener {
            performLogin()
        }
    }

    private fun performLogin() {
        val username = binding.etUsername.text.toString().trim()
        val password = binding.etPassword.text.toString().trim()

        // Validar campos vazios
        if (username.isEmpty()) {
            binding.tilUsername.error = getString(R.string.username_empty)
            return
        } else {
            binding.tilUsername.error = null
        }

        if (password.isEmpty()) {
            binding.tilPassword.error = getString(R.string.password_empty)
            return
        } else {
            binding.tilPassword.error = null
        }

        // Mostrar ProgressBar
        binding.progressBar.visibility = View.VISIBLE
        binding.btnLogin.isEnabled = false

        // Simular verificação de login (em um app real, isso seria uma chamada de API)
        android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({
            binding.progressBar.visibility = View.GONE
            binding.btnLogin.isEnabled = true

            if (validUsers.containsKey(username) && validUsers[username] == password) {
                // Login bem-sucedido
                saveLoginSession(username)
                Toast.makeText(this, R.string.login_success, Toast.LENGTH_SHORT).show()

                val intent = Intent(this, HomeActivity::class.java)
                intent.putExtra("username", username)
                startActivity(intent)
                finish()
            } else {
                // Login falhou
                Toast.makeText(this, R.string.login_failed, Toast.LENGTH_SHORT).show()
            }
        }, 1000) // Simular atraso de 1 segundo
    }

    private fun saveLoginSession(username: String) {
        val sharedPrefs = getSharedPreferences("LoginPrefs", MODE_PRIVATE)
        sharedPrefs.edit().apply {
            putBoolean("isLoggedIn", true)
            putString("username", username)
            apply()
        }
    }

    override fun onBackPressed() {
        // Quando pressionar back, voltar para a tela inicial
        super.onBackPressed()
        val intent = Intent(this, MainActivity::class.java)
        startActivity(intent)
        finish()
    }
}
