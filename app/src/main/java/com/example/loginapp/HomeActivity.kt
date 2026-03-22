package com.example.loginapp

import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.example.loginapp.databinding.ActivityHomeBinding

class HomeActivity : AppCompatActivity() {

    private lateinit var binding: ActivityHomeBinding

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityHomeBinding.inflate(layoutInflater)
        setContentView(binding.root)

        // Recuperar nome do usuário logado
        val sharedPrefs = getSharedPreferences("LoginPrefs", MODE_PRIVATE)
        val username = sharedPrefs.getString("username", "Usuário")

        binding.tvUserInfo.text = getString(R.string.logged_in_as) + username

        binding.btnLogout.setOnClickListener {
            logout()
        }

        binding.btnChats.setOnClickListener {
            val intent = Intent(this, ChatsActivity::class.java)
            startActivity(intent)
        }
    }

    private fun logout() {
        // Limpar sessão
        val sharedPrefs = getSharedPreferences("LoginPrefs", MODE_PRIVATE)
        sharedPrefs.edit().apply {
            clear()
            apply()
        }

        Toast.makeText(this, "Logout realizado com sucesso", Toast.LENGTH_SHORT).show()

        // Voltar para a tela inicial
        val intent = Intent(this, MainActivity::class.java)
        startActivity(intent)
        finish()
    }

    override fun onBackPressed() {
        // Não permitir voltar para a tela anterior após login
        // finish()
        super.onBackPressed()
    }
}
